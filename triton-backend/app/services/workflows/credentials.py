"""Workflow S3 credential management backed by DB records and Kubernetes secrets."""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from sqlmodel import Session

from app.core.identity import require_user_entity
from app.db.entities import WorkflowS3CredentialEntity
from app.exceptions import BadGatewayError, ConflictError, NotFoundError
from app.repositories import workflow_s3_credentials
from app.schemas import (
    CreateWorkflowS3CredentialRequest,
    WorkflowS3CredentialDeleteResponse,
    WorkflowS3CredentialDTO,
)
from app.services.kubernetes_client import api_client, in_cluster_namespace
from app.services.workflows.config import get_config


def list_credentials(session: Session) -> list[WorkflowS3CredentialDTO]:
    rows = workflow_s3_credentials.list_all(session)
    return [_to_dto(row) for row in rows]


def create_credential(
    request: CreateWorkflowS3CredentialRequest,
    session: Session,
    claims: dict[str, object],
) -> WorkflowS3CredentialDTO:
    user = require_user_entity(session, claims)
    namespace = _workflow_namespace()
    name = request.name.strip()
    secret_name = _secret_name(name)

    if workflow_s3_credentials.find_by_name(session, name):
        raise ConflictError(f"Workflow S3 credential '{name}' already exists.")
    if workflow_s3_credentials.find_by_secret_name(session, namespace, secret_name):
        raise ConflictError(f"Kubernetes secret '{secret_name}' is already managed in namespace '{namespace}'.")
    if _secret_exists(namespace, secret_name):
        raise ConflictError(f"Kubernetes secret '{secret_name}' already exists in namespace '{namespace}'.")

    _apply_secret(namespace, secret_name, request.access_key_id, request.secret_access_key)
    try:
        row = workflow_s3_credentials.create(
            session,
            created_by_user_id=user.id or 0,
            name=name,
            namespace=namespace,
            secret_name=secret_name,
            access_key_id=request.access_key_id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    except Exception:
        _delete_secret(namespace, secret_name)
        raise
    return _to_dto(row)


def delete_credential(session: Session, credential_id: int) -> WorkflowS3CredentialDeleteResponse:
    row = workflow_s3_credentials.find_by_id(session, credential_id)
    if not row:
        raise NotFoundError("Workflow S3 credential not found")
    _delete_secret(row.namespace, row.secret_name)
    workflow_s3_credentials.delete(session, row)
    return WorkflowS3CredentialDeleteResponse(
        status="deleted",
        message=f"Deleted workflow S3 credential '{row.name}'.",
        id=credential_id,
    )


def _to_dto(row: WorkflowS3CredentialEntity) -> WorkflowS3CredentialDTO:
    return WorkflowS3CredentialDTO(
        id=row.id or 0,
        name=row.name,
        namespace=row.namespace,
        secret_name=row.secret_name,
        access_key_id=row.access_key_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _secret_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:40].strip("-") or "credential"
    suffix = secrets.token_hex(3)
    return f"workflow-s3-{slug}-{suffix}"[:63].rstrip("-")


def _workflow_namespace() -> str:
    configured = (get_config().namespace or "").strip()
    if configured:
        return configured
    return in_cluster_namespace().strip() or "triton-control"


def _apply_secret(namespace: str, secret_name: str, access_key_id: str, secret_access_key: str) -> None:
    from kubernetes import client  # type: ignore[import-untyped]
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

    body = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            labels={
                "app.kubernetes.io/managed-by": "triton-control",
                "triton-control/component": "workflow-s3-credential",
            },
        ),
        type="Opaque",
        string_data={
            "access-key-id": access_key_id,
            "secret-access-key": secret_access_key,
        },
    )
    core = client.CoreV1Api(api_client())
    try:
        core.create_namespaced_secret(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status == 409:
            raise ConflictError(
                f"Kubernetes secret '{secret_name}' already exists in namespace '{namespace}'."
            ) from exc
        raise BadGatewayError(_k8s_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to create workflow credential secret: {exc}") from exc


def _delete_secret(namespace: str, secret_name: str) -> None:
    from kubernetes import client  # type: ignore[import-untyped]
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

    core = client.CoreV1Api(api_client())
    try:
        core.delete_namespaced_secret(name=secret_name, namespace=namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise BadGatewayError(_k8s_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to delete workflow credential secret: {exc}") from exc


def _secret_exists(namespace: str, secret_name: str) -> bool:
    from kubernetes import client  # type: ignore[import-untyped]
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

    core = client.CoreV1Api(api_client())
    try:
        core.read_namespaced_secret(name=secret_name, namespace=namespace)
        return True
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise BadGatewayError(_k8s_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to verify workflow credential secret: {exc}") from exc


def _k8s_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    details = f"{reason} - {body}" if reason and body else reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
