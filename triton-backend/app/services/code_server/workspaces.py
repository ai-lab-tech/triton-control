"""Use cases for per-user code-server workspaces on Kubernetes."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlmodel import Session

from app.core.identity import require_user_entity
from app.db.entities import CodeServerEntity
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.repositories import code_servers
from app.schemas import CodeServerDeleteResponse, CodeServerDTO, CreateCodeServerRequest
from app.services.code_server import kubernetes as k8s
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster
from app.services.oidc.config import kubernetes_enabled


def list_code_servers(session: Session, claims: dict[str, Any]) -> list[CodeServerDTO]:
    user = require_user_entity(session, claims)
    rows = code_servers.list_for_owner(session, user.id or 0)
    for row in rows:
        _refresh_status(session, row)
    return [_to_dto(row) for row in rows]


def create_code_server(
    request: CreateCodeServerRequest,
    session: Session,
    claims: dict[str, Any],
) -> CodeServerDTO:
    _require_kubernetes_enabled()
    user = require_user_entity(session, claims)
    owner_id = user.id or 0
    name = request.name
    resource_prefix = _resource_prefix(owner_id, name)
    namespace = _workspace_namespace()
    statefulset_name = resource_prefix
    service_name = f"{resource_prefix}-svc"
    secret_name = f"{resource_prefix}-secret"
    applied_resources = k8s.apply_code_server_resources(
        request,
        namespace=namespace,
        statefulset_name=statefulset_name,
        service_name=service_name,
        secret_name=secret_name,
    )

    row = code_servers.find_by_owner_and_name(session, owner_id, name)
    values = {
        "namespace": namespace,
        "statefulset_name": statefulset_name,
        "service_name": service_name,
        "secret_name": secret_name,
        "image": request.image,
        "url": "",
        "password_enc": "",
        "status": "creating",
        "status_message": "Kubernetes resources applied; waiting for pod readiness.",
        "applied_resources": applied_resources,
        "updated_at": datetime.utcnow(),
    }
    if row:
        _ensure_owner(row, owner_id)
        for key, value in values.items():
            setattr(row, key, value)
        row = code_servers.save(session, row)
    else:
        row = code_servers.create(
            session,
            owner_user_id=owner_id,
            name=name,
            **values,
        )
    if row.id:
        row.url = proxy_url(row.id)
        row = code_servers.save(session, row)
    return _to_dto(row)


def get_code_server(session: Session, claims: dict[str, Any], code_server_id: int) -> CodeServerDTO:
    row = _get_owned(session, claims, code_server_id)
    _refresh_status(session, row)
    return _to_dto(row)


def get_owned_code_server(session: Session, claims: dict[str, Any], code_server_id: int) -> CodeServerEntity:
    """Return an owned code-server entity for internal proxy use."""
    return _get_owned(session, claims, code_server_id)


def delete_code_server(
    session: Session,
    claims: dict[str, Any],
    code_server_id: int,
) -> CodeServerDeleteResponse:
    _require_kubernetes_enabled()
    row = _get_owned(session, claims, code_server_id)
    namespace = row.namespace
    try:
        message = k8s.delete_code_server_resources(
            namespace=namespace,
            statefulset_name=row.statefulset_name,
            service_name=row.service_name,
            secret_name=row.secret_name,
        )
    except Exception as exc:
        raise BadRequestError("Kubernetes configuration could not be loaded") from exc
    code_servers.delete(session, row)
    return CodeServerDeleteResponse(status="deleted", message=message, namespace=namespace)


def _get_owned(session: Session, claims: dict[str, Any], code_server_id: int) -> CodeServerEntity:
    user = require_user_entity(session, claims)
    row = code_servers.find_by_id(session, code_server_id)
    if not row:
        raise NotFoundError("Code server not found")
    _ensure_owner(row, user.id or 0)
    return row


def _ensure_owner(row: CodeServerEntity, owner_id: int) -> None:
    if row.owner_user_id != owner_id:
        raise ForbiddenError("Code server access denied")


def _refresh_status(session: Session, row: CodeServerEntity) -> None:
    if not kubernetes_enabled():
        status = "unavailable"
        message = "Kubernetes is disabled for Triton Control; workspace status cannot be refreshed."
        if status != row.status or message != row.status_message:
            row.status = status
            row.status_message = message
            row.updated_at = datetime.utcnow()
            code_servers.save(session, row)
        return
    try:
        status, message = k8s.read_status(row.namespace, row.statefulset_name)
    except Exception:
        return
    if status != row.status or message != row.status_message:
        row.status = status
        row.status_message = message
        row.updated_at = datetime.utcnow()
        code_servers.save(session, row)


def _to_dto(row: CodeServerEntity) -> CodeServerDTO:
    return CodeServerDTO(
        id=row.id or 0,
        name=row.name,
        namespace=row.namespace,
        statefulset_name=row.statefulset_name,
        service_name=row.service_name,
        image=row.image,
        url=proxy_url(row.id or 0),
        status=row.status,
        status_message=row.status_message,
        applied_resources=row.applied_resources or [],
    )


def _require_kubernetes_enabled() -> None:
    if not kubernetes_enabled():
        raise BadRequestError(
            "Code server is available only when Kubernetes is enabled for Triton Control",
        )


def _workspace_namespace() -> str:
    control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
    if control_ns:
        return control_ns
    configured = (
        os.getenv("TRITON_CONTROL_NAMESPACE")
        or os.getenv("KUBERNETES_NAMESPACE")
        or os.getenv("POD_NAMESPACE")
        or ""
    ).strip()
    return configured or "triton-control"


def _resource_prefix(owner_id: int, name: str) -> str:
    base = f"code-{owner_id}-{name}"
    return base[:52].rstrip("-")


def proxy_url(code_server_id: int) -> str:
    return f"/api/code-servers/{code_server_id}/proxy/"
