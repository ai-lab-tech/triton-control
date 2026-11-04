"""Prepare owned workflow storage and automatic tracking/deployment credentials."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

import yaml  # type: ignore[import-untyped]
from kubernetes import client  # type: ignore[import-untyped]
from sqlmodel import Session

from app.core.access_control import require_member_or_admin
from app.core.identity import require_user_entity
from app.core.user_auth import issue_access_token
from app.db.database import session_factory
from app.exceptions import BadRequestError, NotFoundError
from app.repositories import s3_profiles, workflow_s3_credentials
from app.services.kubernetes_client import api_client
from app.services.mlflow import tracking
from app.services.workflows.artifact_repository import REPOSITORY_KEY
from app.services.workflows.tracking_policy import validate_workflow
from app.services.workflows.workspace_access import OWNER_LABEL

_SUBMIT_PATH = re.compile(r"api/v1/workflows/([a-z0-9-]+)\Z")


def prepare_submission(path: str, body: bytes, claims: dict[str, Any]) -> tuple[bytes, str | None, str | None]:
    """Give every container the owner's tracking and deployment credentials."""
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
    profile_name = None
    with session_factory() as session:
        user = require_user_entity(session, claims)
        owner_id = user.id
        linked = [
            row for row in workflow_s3_credentials.list_all(session)
            if row.namespace == namespace and row.created_by_user_id == owner_id
        ]
        allowed_secrets = {row.secret_name for row in linked}
        _select_artifact_repository(workflow, session, namespace, owner_id or 0)
        reference = workflow.get("spec", {}).get("artifactRepositoryRef")
        if isinstance(reference, dict):
            selected = next((row for row in linked if row.secret_name == reference.get("configMap")), None)
            if selected and selected.s3_profile_id:
                profile = s3_profiles.find_for_owner(session, owner_id or 0, selected.s3_profile_id)
                profile_name = profile.name if profile else None
        identity = {
            "email": user.email, "name": user.name, "role": user.role,
            "auth_provider": user.auth_provider, "credential_version": user.credential_version,
        }
    validate_workflow(workflow, allowed_secrets)
    repository_ref = workflow.get("spec", {}).get("artifactRepositoryRef")
    if repository_ref:
        repository = client.CoreV1Api(api_client()).read_namespaced_config_map(
            name=repository_ref["configMap"], namespace=namespace,
        )
        try:
            resolved = yaml.safe_load((repository.data or {})[REPOSITORY_KEY])
        except (KeyError, yaml.YAMLError) as exc:
            raise BadRequestError("Invalid linked S3 artifact repository") from exc
        if not isinstance(resolved, dict):
            raise BadRequestError("Invalid linked S3 artifact repository")
        validate_workflow({"spec": resolved}, allowed_secrets)
    metadata = workflow.get("metadata") or {}
    prefix = (metadata.get("name") or metadata.get("generateName") or "workflow")[:32].rstrip("-")
    secret_name = f"mlflow-{prefix}-{secrets.token_hex(5)}"
    tracking_token = secrets.token_urlsafe(32)
    deployment_token = issue_access_token(
        identity, expires_minutes=60,
        extra_claims={"token_use": "workflow_deployment"},  # nosec B105 -- credential purpose
    )
    managed = {
        "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN", "TRITON_CONTROL_URL", "TRITON_CONTROL_TOKEN",
    }
    if profile_name:
        managed.add("TRITON_CONTROL_S3_PROFILE_NAME")
    for template in workflow.get("spec", {}).get("templates", []):
        pod = template.get("container") or template.get("script")
        if pod is None:
            continue
        environment = pod.setdefault("env", [])
        environment[:] = [item for item in environment if item.get("name") not in managed]
        environment.extend([
            {"name": "TRITON_CONTROL_URL", "value": tracking.control_url()},
            {"name": "MLFLOW_TRACKING_URI", "value": tracking.tracking_uri("workflows", namespace, secret_name)},
            {"name": "MLFLOW_TRACKING_TOKEN", "valueFrom": {
                "secretKeyRef": {"name": secret_name, "key": tracking.TRACKING_TOKEN_KEY},
            }},
            {"name": "TRITON_CONTROL_TOKEN", "valueFrom": {
                "secretKeyRef": {"name": secret_name, "key": "token"},
            }},
        ])
        if profile_name:
            environment.append({"name": "TRITON_CONTROL_S3_PROFILE_NAME", "value": profile_name})
    metadata.setdefault("annotations", {})[tracking.WORKFLOW_OWNER_ANNOTATION] = str(owner_id)
    metadata.setdefault("labels", {})[OWNER_LABEL] = str(owner_id)
    workflow["metadata"] = metadata
    client.CoreV1Api(api_client()).create_namespaced_secret(namespace=namespace, body=client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=secret_name, namespace=namespace,
            annotations={tracking.WORKFLOW_OWNER_ANNOTATION: str(owner_id)},
        ),
        string_data={tracking.TRACKING_TOKEN_KEY: tracking_token, "token": deployment_token}, type="Opaque",
    ))
    return json.dumps(payload).encode(), secret_name, namespace


def _needs_artifact_repository(value: Any) -> bool:
    """Find S3 artifacts that rely on repository defaults instead of inline storage."""
    if isinstance(value, list):
        return any(_needs_artifact_repository(item) for item in value)
    if not isinstance(value, dict):
        return False
    storage = value.get("s3")
    if isinstance(storage, dict) and (not storage.get("bucket") or not storage.get("endpoint")):
        return True
    return any(_needs_artifact_repository(item) for item in value.values())


def _select_artifact_repository(
    workflow: dict[str, Any], session: Session, namespace: str, owner_id: int,
) -> None:
    """Select only a repository linked to a profile owned by the submitter."""
    metadata = workflow.get("metadata") or {}
    annotations = metadata.get("annotations") or {}
    spec = workflow.get("spec") or {}
    if spec.get("artifactRepositoryRef"):
        return
    profile_name = annotations.get("triton-control.ai/mlflow-s3-profile-name", "").strip()
    profile_id_raw = annotations.get("triton-control.ai/mlflow-s3-profile-id", "")
    if not (profile_name or profile_id_raw or _needs_artifact_repository(spec)):
        return
    if profile_name and profile_id_raw:
        raise BadRequestError("Specify exactly one MLflow S3 profile name or ID")
    profile = None
    if profile_name:
        profile = s3_profiles.find_by_name_for_owner(session, owner_id, profile_name)
        if profile is None:
            raise NotFoundError("S3 profile not found")
    elif profile_id_raw:
        try:
            profile_id = int(profile_id_raw)
        except (TypeError, ValueError) as exc:
            raise BadRequestError("A valid MLflow S3 profile ID is required") from exc
        profile = s3_profiles.find_for_owner(session, owner_id, profile_id)
        if profile is None:
            raise NotFoundError("S3 profile not found")
    linked = [
        row for row in workflow_s3_credentials.list_all(session)
        if row.namespace == namespace and row.created_by_user_id == owner_id
        and row.s3_profile_id is not None
        and s3_profiles.find_for_owner(session, owner_id, row.s3_profile_id) is not None
        and (profile is None or row.s3_profile_id == profile.id)
    ]
    if not linked:
        raise BadRequestError(
            "Link an S3 profile under Workflows -> Configure S3 Secrets before submitting this workflow"
        )
    if len(linked) > 1:
        raise BadRequestError(
            "Multiple linked S3 repositories are available; select a profile using "
            "the triton-control.ai/mlflow-s3-profile-name annotation or provide artifactRepositoryRef"
        )
    selected = linked[0]
    spec["artifactRepositoryRef"] = {"configMap": selected.secret_name, "key": REPOSITORY_KEY}
    workflow["spec"] = spec

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
