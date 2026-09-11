"""Workflow S3 credential management backed by DB records and Kubernetes secrets."""

from __future__ import annotations

import re
import secrets
from datetime import datetime

from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.crypto import decrypt_secret
from app.core.identity import require_user_entity
from app.db.entities import S3ProfileEntity, WorkflowS3CredentialEntity
from app.exceptions import BadGatewayError, BadRequestError, ConflictError, NotFoundError
from app.repositories import s3_profiles, workflow_s3_credentials
from app.schemas import (
    CreateWorkflowS3CredentialRequest,
    WorkflowS3CredentialDeleteResponse,
    WorkflowS3CredentialDTO,
)
from app.services.kubernetes_client import api_client, in_cluster_namespace
from app.services.workflows.artifact_repository import REPOSITORY_KEY, delete_repository, sync_repository
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
    profile = None
    if request.s3_profile_id is not None:
        profile = s3_profiles.find_for_owner(session, user.id or 0, request.s3_profile_id)
        if not profile:
            raise NotFoundError("S3 profile not found")
        try:
            request = CreateWorkflowS3CredentialRequest(
                name=request.name, access_key_id=profile.access_key,
                secret_access_key=decrypt_secret(profile.secret_key_enc),
                ca_certificate=profile.ca_certificate or "",
            )
        except ValidationError as exc:
            if any(error["loc"] == ("ca_certificate",) for error in exc.errors(include_input=False)):
                raise BadRequestError(
                    "The S3 profile's CA certificate is not valid PEM. In S3 Profiles, check that it starts with "
                    "-----BEGIN CERTIFICATE----- and ends with -----END CERTIFICATE----- (five dashes)."
                ) from None
            raise BadRequestError("The S3 profile requires a non-empty access key and secret key.") from None
        except ValueError:
            raise BadRequestError(
                "The S3 profile's secret key could not be read. Save it again in S3 Profiles."
            ) from None
    namespace = _workflow_namespace()
    name = request.name.strip()
    secret_name = _secret_name(name)

    if workflow_s3_credentials.find_by_name(session, name):
        raise ConflictError(f"Workflow S3 credential '{name}' already exists.")
    if workflow_s3_credentials.find_by_secret_name(session, namespace, secret_name):
        raise ConflictError(f"Kubernetes secret '{secret_name}' is already managed in namespace '{namespace}'.")
    if _secret_exists(namespace, secret_name):
        raise ConflictError(f"Kubernetes secret '{secret_name}' already exists in namespace '{namespace}'.")

    _apply_secret(namespace, secret_name, request.access_key_id, request.secret_access_key, request.ca_certificate)
    try:
        row = workflow_s3_credentials.create(
            session,
            created_by_user_id=user.id or 0,
            name=name,
            namespace=namespace,
            secret_name=secret_name,
            access_key_id=request.access_key_id,
            s3_profile_id=profile.id if profile else None,
            s3_profile_name=profile.name if profile else "",
            sync_error="S3 configuration synchronization pending." if profile else "",
            last_synced_at=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    except Exception:
        _delete_secret(namespace, secret_name)
        raise
    if profile:
        sync_profile_credentials(session, profile.id)
        session.refresh(row)
    return _to_dto(row)


def delete_credential(session: Session, credential_id: int) -> WorkflowS3CredentialDeleteResponse:
    row = workflow_s3_credentials.find_by_id(session, credential_id)
    if not row:
        raise NotFoundError("Workflow S3 credential not found")
    if getattr(row, "s3_profile_id", None):
        delete_repository(row)
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
        artifact_repository_config_map=row.secret_name if getattr(row, "s3_profile_id", None) else None,
        artifact_repository_key=REPOSITORY_KEY if getattr(row, "s3_profile_id", None) else None,
        access_key_id=row.access_key_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        s3_profile_id=getattr(row, "s3_profile_id", None),
        s3_profile_name=getattr(row, "s3_profile_name", ""),
        sync_error=getattr(row, "sync_error", ""),
        last_synced_at=getattr(row, "last_synced_at", None),
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


def _apply_secret(
    namespace: str, secret_name: str, access_key_id: str, secret_access_key: str, ca_certificate: str = "",
) -> None:
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
    if ca_certificate:
        body.string_data["ca.pem"] = ca_certificate
    core = client.CoreV1Api(api_client())
    try:
        core.create_namespaced_secret(namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status == 409:
            raise ConflictError(
                f"Kubernetes secret '{secret_name}' already exists in namespace '{namespace}'."
            ) from exc
        raise BadGatewayError(
            f"Failed to create workflow credential Secret (Kubernetes status {exc.status})."
        ) from None
    except Exception:
        raise BadGatewayError("Failed to create workflow credential Secret.") from None


def _delete_secret(namespace: str, secret_name: str) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    core = client.CoreV1Api(api_client())
    try:
        core.delete_namespaced_secret(name=secret_name, namespace=namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise BadGatewayError(_k8s_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to delete workflow credential secret: {exc}") from exc


def _secret_exists(namespace: str, secret_name: str) -> bool:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

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


def sync_profile_credentials(session: Session, profile_id: int) -> None:
    """Serialize synchronization and read the latest committed profile values."""
    profile = session.exec(select(S3ProfileEntity).where(
        S3ProfileEntity.id == profile_id,
    ).with_for_update().execution_options(populate_existing=True)).first()
    if not profile:
        return
    for row in workflow_s3_credentials.list_for_profile(session, profile_id):
        row.s3_profile_name = profile.name
        try:
            request = CreateWorkflowS3CredentialRequest(
                name=row.name, access_key_id=profile.access_key,
                secret_access_key=decrypt_secret(profile.secret_key_enc),
                ca_certificate=profile.ca_certificate or "",
            )
            _replace_secret(row, request)
            sync_repository(row, profile)
        except Exception:
            # Never persist exception text: API errors can contain Secret data.
            row.sync_error = (
                "S3 sync failed. Check the profile endpoint, certificate, and Kubernetes access, then retry."
            )
        else:
            row.access_key_id = request.access_key_id
            row.sync_error = ""
            row.last_synced_at = datetime.utcnow()
            row.updated_at = row.last_synced_at
        session.add(row)
    session.commit()


def retry_sync(session: Session, claims: dict[str, object], credential_id: int) -> WorkflowS3CredentialDTO:
    user = require_user_entity(session, claims)
    row = workflow_s3_credentials.find_by_id(session, credential_id)
    if not row or not row.s3_profile_id:
        raise NotFoundError("Linked workflow credential not found")
    if not s3_profiles.find_for_owner(session, user.id or 0, row.s3_profile_id):
        raise NotFoundError("S3 profile not found")
    sync_profile_credentials(session, row.s3_profile_id)
    session.refresh(row)
    return _to_dto(row)


def _replace_secret(row: WorkflowS3CredentialEntity, request: CreateWorkflowS3CredentialRequest) -> None:
    import base64

    from kubernetes import client

    core = client.CoreV1Api(api_client())
    body = core.read_namespaced_secret(name=row.secret_name, namespace=row.namespace)
    labels = body.metadata.labels or {}
    if (labels.get("app.kubernetes.io/managed-by") != "triton-control"
            or labels.get("triton-control/component") != "workflow-s3-credential"):
        raise ConflictError("Refusing to update an unmanaged Secret")
    data = dict(body.data or {})
    data["access-key-id"] = base64.b64encode(request.access_key_id.encode()).decode()
    data["secret-access-key"] = base64.b64encode(request.secret_access_key.encode()).decode()
    if request.ca_certificate:
        data["ca.pem"] = base64.b64encode(request.ca_certificate.encode()).decode()
    else:
        data.pop("ca.pem", None)
    body.data = data
    body.string_data = None
    core.replace_namespaced_secret(name=row.secret_name, namespace=row.namespace, body=body)
