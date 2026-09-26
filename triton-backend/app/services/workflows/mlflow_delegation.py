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
from app.repositories import s3_profiles
from app.services.kubernetes_client import api_client, in_cluster_namespace

_SUBMIT_PATH = re.compile(r"api/v1/workflows/([a-z0-9-]+)\Z")
_DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_ANNOTATION = "triton-control.ai/mlflow-deploy-template"


def prepare_submission(path: str, body: bytes, claims: dict[str, Any]) -> tuple[bytes, str | None, str | None]:
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

    spec = workflow.get("spec") or {}
    templates = spec.get("templates") or []
    selected = next((item for item in templates if item.get("name") == template_name), None)
    if not isinstance(selected, dict):
        raise BadRequestError("MLflow deployment template not found")
    pod_template = selected.get("script") or selected.get("container")
    if not isinstance(pod_template, dict):
        raise BadRequestError("MLflow deployment template must be a script or container")
    environment = pod_template.setdefault("env", [])
    reserved_env = {"TRITON_CONTROL_TOKEN", "TRITON_CONTROL_S3_PROFILE_ID"}
    if any(item.get("name") in reserved_env for item in environment):
        raise BadRequestError("A managed Triton Control environment variable is already set")

    token = issue_access_token(
        {
            "email": user.email, "name": user.name, "role": user.role,
            "auth_provider": user.auth_provider, "credential_version": user.credential_version,
        },
        expires_minutes=60,
        extra_claims={
            "token_use": "mlflow_workflow",
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
