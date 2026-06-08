"""Database updates for Kubernetes-managed Triton deployment records.

Public surface: ``upsert_deployed_instance``, ``delete_instance_record``,
``update_instance_after_apply``, and ``record_deployment_failure``. These
functions persist deployment state, S3 metadata, ownership assignments, and
deployment logs into the database.

Repository-facing helper module for the deployment service; no HTTP handlers or
Kubernetes client calls.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sqlmodel import Session

from app.core.crypto import encrypt_secret, hash_secret
from app.core.identity import require_user_entity
from app.db.database import session_factory
from app.exceptions import BadRequestError
from app.repositories import dashboard_alerts, instances, users
from app.repositories import perf_analyzer as perf_analyzer_repo
from app.schemas import CreateDeploymentRequest


def delete_instance_record(session: Session, instance: Any) -> None:
    if instance.id is None:
        raise BadRequestError("Instance has no database id")
    perf_analyzer_repo.delete_runs_for_instance(session, instance.id)
    dashboard_alerts.delete_for_instance(session, instance.id, instance.name)
    for user in users.list_all(session):
        assigned = user.assigned_instances or []
        if instance.name in assigned:
            user.assigned_instances = [n for n in assigned if n != instance.name]
            users.save(session, user)
    instances.delete(session, instance)


def upsert_deployed_instance(
    request: CreateDeploymentRequest,
    session: Session,
    claims: dict[str, Any],
    *,
    namespace: str,
    deployment_name: str,
    service_name: str,
    secret_name: str,
    image: str,
    triton_url: str,
    metrics_url: str | None,
    initial_snapshot: dict[str, Any],
) -> Any:
    user = require_user_entity(session, claims)
    s3 = _parse_s3_url(request.s3_url)
    snap = initial_snapshot
    values = {
        "url": triton_url,
        "name": deployment_name,
        "model_names": snap["model_names"],
        "server_metadata": snap["server_metadata"] or {"name": "triton", "deployment": "kubernetes", "image": image},
        "health_live": snap["health_live"],
        "health_ready": snap["health_ready"],
        "health_last_checked_at": snap["checked_at"],
        "health_error": snap["health_error"],
        "metrics_url": metrics_url,
        "deployment_runtime": "kubernetes",
        "deployment_namespace": namespace,
        "deployment_name": deployment_name,
        "deployment_service_name": service_name,
        "deployment_secret_name": secret_name,
        "deployment_log": (
            f"Namespace: {namespace}\nDeployment: {deployment_name}\nService: {service_name}\n"
            f"Image: {image}\nModel repository: {request.s3_url}\nTriton URL: {triton_url}\n"
            f"Initial health: {snap['health_error'] or 'healthy'}"
        ),
        "is_self_deployed": True,
        "s3_enabled": True,
        "s3_endpoint": s3["endpoint"],
        "s3_bucket": s3["bucket"],
        "s3_prefix": s3["prefix"],
        "s3_use_https": s3["use_https"],
        "s3_verify_ssl": s3["use_https"],
        "s3_ca_certificate": (request.s3_ca_certificate or "") if s3["use_https"] else "",
        "s3_region": request.s3_region,
        "s3_access_key": request.s3_access_key,
        "s3_secret_key_hash": hash_secret(request.s3_secret_key),
        "s3_secret_key_enc": encrypt_secret(request.s3_secret_key),
    }
    row = instances.find_by_name(session, deployment_name) or instances.find_by_url(session, triton_url)
    if row:
        for key, value in values.items():
            setattr(row, key, value)
        if row.created_by_user_id is None:
            row.created_by_user_id = user.id
        instances.save(session, row)
    else:
        row = instances.create(session, created_by_user_id=user.id, **values)
    if deployment_name not in (user.assigned_instances or []):
        user.assigned_instances = [*user.assigned_instances, deployment_name]
        users.save(session, user)
    return row


def update_instance_after_apply(
    instance_id: int, *, triton_url: str, metrics_url: str, applied_resources: list[str]
) -> None:
    status = "Kubernetes resources applied; waiting for Triton readiness."
    _patch(
        instance_id,
        url=triton_url,
        metrics_url=metrics_url,
        health_error=status,
        deployment_log_append=(
            f"Applied Kubernetes resources: {', '.join(applied_resources)}",
            f"Triton URL: {triton_url}",
            status,
        ),
    )


def record_deployment_failure(instance_id: int, message: str) -> None:
    _patch(instance_id, health_error=message, deployment_log_append=(message,))


def _patch(instance_id: int, *, deployment_log_append: tuple[str, ...] = (), **fields: Any) -> None:
    with session_factory() as session:
        row = instances.find_by_id(session, instance_id)
        if not row:
            return
        if deployment_log_append:
            parts = [
                (row.deployment_log or "").strip(),
                *[line.strip() for line in deployment_log_append if line.strip()],
            ]
            fields["deployment_log"] = "\n".join(part for part in parts if part)
        for key, value in fields.items():
            setattr(row, key, value)
        instances.save(session, row)


def _parse_s3_url(s3_url: str) -> dict[str, str | bool | None]:
    value = s3_url.removeprefix("s3://").strip("/")
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        scheme = parsed.scheme
        if scheme == "http" and parsed.port == 443:
            scheme = "https"
        parts = [p for p in parsed.path.split("/") if p]
        return {
            "endpoint": f"{scheme}://{parsed.netloc}",
            "bucket": parts[0] if parts else "",
            "prefix": "/".join(parts[1:]) or None,
            "use_https": scheme == "https",
        }
    parts = [p for p in value.split("/") if p]
    return {
        "endpoint": None,
        "bucket": parts[0] if parts else "",
        "prefix": "/".join(parts[1:]) or None,
        "use_https": False,
    }
