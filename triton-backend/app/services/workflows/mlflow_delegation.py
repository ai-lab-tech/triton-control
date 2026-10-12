"""Add a narrowly scoped deployment token to an opted-in Argo workflow."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from kubernetes import client  # type: ignore[import-untyped]

from app.core.access_control import require_member_or_admin
from app.core.identity import require_user_entity
from app.core.user_auth import issue_access_token
from app.db.database import session_factory
from app.exceptions import BadRequestError, NotFoundError
from app.repositories import s3_profiles, workflow_s3_credentials
from app.services.kubernetes_client import api_client, in_cluster_namespace
from app.services.mlflow import tracking
from app.services.workflows.artifact_repository import REPOSITORY_KEY
from app.services.workflows.tracking_policy import validate_workflow

_SUBMIT_PATH = re.compile(r"api/v1/workflows/([a-z0-9-]+)\Z")
_DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ANNOTATION = "triton-control.ai/mlflow-deploy-template"


def prepare_submission(path: str, body: bytes, claims: dict[str, Any]) -> tuple[bytes, str | None, str | None]:
    """Give every submitted container/script the submitter's tracking identity."""
    match = _SUBMIT_PATH.fullmatch(path.strip("/"))
    if not match:
        return body, None, None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body, None, None
    workflow = payload.get("workflow") if isinstance(payload, dict) else None
    if not isinstance(workflow, dict):
        return body, None, None
    require_member_or_admin(claims)
    namespace = match.group(1)
    with session_factory() as session:
        user = require_user_entity(session, claims)
        owner_id = user.id
        allowed_secrets = {
            row.secret_name for row in workflow_s3_credentials.list_all(session)
            if row.namespace == namespace and row.created_by_user_id == owner_id
        }
    validate_workflow(workflow, allowed_secrets)
    body, secret_name, _ = _prepare_deployment_submission(path, body, claims)
    has_deployment_secret = secret_name is not None
    payload = json.loads(body)
    workflow = payload["workflow"]
    metadata = workflow.get("metadata") or {}
    if not secret_name:
        prefix = (metadata.get("name") or metadata.get("generateName") or "workflow")[:32].rstrip("-")
        secret_name = f"mlflow-{prefix}-{secrets.token_hex(5)}"
    token = secrets.token_urlsafe(32)
    core = client.CoreV1Api(api_client())
    secret_body = {
        "metadata": {"annotations": {tracking.WORKFLOW_OWNER_ANNOTATION: str(owner_id)}},
        "stringData": {tracking.TRACKING_TOKEN_KEY: token},
    }
    try:
        for template in workflow.get("spec", {}).get("templates", []):
            pod = template.get("container") or template.get("script")
            if pod is None:
                continue
            environment = pod.setdefault("env", [])
            environment[:] = [
                item for item in environment
                if item.get("name") not in {"MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN"}
            ]
            environment.extend([
                {"name": "MLFLOW_TRACKING_URI", "value": tracking.tracking_uri("workflows", namespace, secret_name)},
                {"name": "MLFLOW_TRACKING_TOKEN", "valueFrom": {
                    "secretKeyRef": {"name": secret_name, "key": tracking.TRACKING_TOKEN_KEY},
                }},
            ])
        if tracking.WORKFLOW_OWNER_ANNOTATION in metadata.get("annotations", {}):
            raise BadRequestError("MLflow workflow owner is managed by Triton Control")
        metadata.setdefault("annotations", {})[tracking.WORKFLOW_OWNER_ANNOTATION] = str(owner_id)
        workflow["metadata"] = metadata
        if has_deployment_secret:
            # Keep the deployment credential in its existing Secret.
            core.patch_namespaced_secret(secret_name, namespace, secret_body)
        else:
            core.create_namespaced_secret(namespace=namespace, body=client.V1Secret(
                metadata=client.V1ObjectMeta(
                    name=secret_name, namespace=namespace,
                    annotations={tracking.WORKFLOW_OWNER_ANNOTATION: str(owner_id)},
                ),
                string_data={tracking.TRACKING_TOKEN_KEY: token}, type="Opaque",
            ))
    except Exception:
        # A deployment Secret may already have been created by the legacy path.
        if has_deployment_secret:
            cleanup_secret(namespace, secret_name)
        raise
    return json.dumps(payload).encode(), secret_name, namespace


def _prepare_deployment_submission(
    path: str, body: bytes, claims: dict[str, Any],
) -> tuple[bytes, str | None, str | None]:
    """Return modified Argo request, secret name, and namespace when opted in."""
    match = _SUBMIT_PATH.fullmatch(path.strip("/"))
    if not match:
        return body, None, None
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body, None, None
    workflow = payload.get("workflow") if isinstance(payload, dict) else None
    if not isinstance(workflow, dict):
        return body, None, None
    metadata = workflow.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    template_name = annotations.get(_ANNOTATION)
    if not template_name:
        return body, None, None

    require_member_or_admin(claims)
    namespace = match.group(1)
    if namespace != in_cluster_namespace():
        raise BadRequestError("MLflow deployment workflows must run in the Triton Control namespace")
    workflow_name = metadata.get("name", "")
    deployment_name = annotations.get("triton-control.ai/mlflow-deployment-name", "")
    profile_name = annotations.get("triton-control.ai/mlflow-s3-profile-name", "").strip()
    profile_id_raw = annotations.get("triton-control.ai/mlflow-s3-profile-id", "")
    if not all(_DNS_NAME.fullmatch(value) for value in (workflow_name, deployment_name)):
        raise BadRequestError("Workflow and deployment names must be lowercase Kubernetes names")
    if bool(profile_name) == bool(profile_id_raw):
        raise BadRequestError("Specify exactly one MLflow S3 profile name or ID")

    with session_factory() as session:
        user = require_user_entity(session, claims)
        if profile_name:
            profile = s3_profiles.find_by_name_for_owner(session, user.id or 0, profile_name)
        else:
            try:
                profile_id = int(profile_id_raw)
            except (TypeError, ValueError) as exc:
                raise BadRequestError("A valid MLflow S3 profile ID is required") from exc
            if profile_id <= 0:
                raise BadRequestError("A valid MLflow S3 profile ID is required")
            profile = s3_profiles.find_for_owner(session, user.id or 0, profile_id)
        if profile is None:
            raise NotFoundError("S3 profile not found")
        profile_id = profile.id or 0
        profile_bucket = profile.bucket
        linked_repositories = [
            row for row in workflow_s3_credentials.list_for_profile(session, profile_id)
            if row.namespace == namespace
        ]
        if not linked_repositories:
            raise BadRequestError(
                "Link the selected S3 profile under Workflows -> Configure S3 Secrets before submitting this workflow"
            )
        if len(linked_repositories) > 1:
            raise BadRequestError("The selected S3 profile has multiple Argo artifact repositories in this namespace")
        repository_config_map = linked_repositories[0].secret_name

    spec = workflow.get("spec") or {}
    spec["artifactRepositoryRef"] = {
        "configMap": repository_config_map,
        "key": REPOSITORY_KEY,
    }
    templates = spec.get("templates") or []
    selected = next((item for item in templates if item.get("name") == template_name), None)
    if not isinstance(selected, dict):
        raise BadRequestError("MLflow deployment template not found")
    pod_template = selected.get("script") or selected.get("container")
    if not isinstance(pod_template, dict):
        raise BadRequestError("MLflow deployment template must be a script or container")
    environment = pod_template.setdefault("env", [])
    reserved_env = {
        "TRITON_CONTROL_TOKEN",
        "TRITON_CONTROL_S3_PROFILE_ID",
        "TRITON_CONTROL_S3_BUCKET",
    }
    if any(item.get("name") in reserved_env for item in environment):
        raise BadRequestError("A managed Triton Control environment variable is already set")

    token = issue_access_token(
        {
            "email": user.email, "name": user.name, "role": user.role,
            "auth_provider": user.auth_provider, "credential_version": user.credential_version,
        },
        expires_minutes=60,
        extra_claims={
            "token_use": "mlflow_workflow",  # nosec B105 - token type claim, not a secret
            "workflow_name": workflow_name,
            "allowed_deployment_name": deployment_name,
            "allowed_s3_profile_id": profile_id,
        },
    )
    secret_name = f"mlflow-{workflow_name[:40]}-{secrets.token_hex(5)}"
    client.CoreV1Api(api_client()).create_namespaced_secret(
        namespace=namespace,
        body=client.V1Secret(
            metadata=client.V1ObjectMeta(name=secret_name, namespace=namespace),
            string_data={"token": token},
            type="Opaque",
        ),
    )
    environment.append({
        "name": "TRITON_CONTROL_TOKEN",
        "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "token"}},
    })
    environment.append({"name": "TRITON_CONTROL_S3_PROFILE_ID", "value": str(profile_id)})
    environment.append({"name": "TRITON_CONTROL_S3_BUCKET", "value": profile_bucket})
    # The workflow and its temporary credential are garbage-collected together.
    spec.setdefault("ttlStrategy", {}).setdefault("secondsAfterCompletion", 300)
    return json.dumps(payload).encode("utf-8"), secret_name, namespace


def cleanup_secret(namespace: str, name: str) -> None:
    client.CoreV1Api(api_client()).delete_namespaced_secret(name=name, namespace=namespace)


def attach_workflow_owner(namespace: str, name: str, workflow_name: str, workflow_uid: str) -> None:
    client.CoreV1Api(api_client()).patch_namespaced_secret(
        name=name,
        namespace=namespace,
        body={"metadata": {"ownerReferences": [{
            "apiVersion": "argoproj.io/v1alpha1", "kind": "Workflow",
            "name": workflow_name, "uid": workflow_uid,
        }]}},
    )
