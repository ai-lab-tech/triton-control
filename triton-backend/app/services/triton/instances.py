"""Business logic for Triton instance lifecycle management.

Provides use cases that combine DB persistence with live Triton connectivity:
  ``create_instance(request, session, claims)`` — connects to the Triton URL,
    reads available models, upserts the ``TritonInstanceEntity``, and returns
    the created DTO.  Raises ``ConflictError`` on duplicate URL/name.
  ``list_instances(session, claims)``           — returns instances visible to
    the caller (all for admins, assigned only for regular users).
  ``get_instance(session, claims, id)``         — single instance fetch with
    access enforcement.
  ``delete_instance(session, claims, id)``      — remove an instance; admin or
    assigned users only.
  ``update_instance(session, claims, id, ...)`` — partial update of instance
    fields.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from app.core.access_control import is_admin, require_admin, require_member_or_admin
from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, ConflictError, NotFoundError, ServiceUnavailableError
from app.mappers import entity_to_dto
from app.repositories import dashboard_alerts, instances, users
from app.repositories import perf_analyzer as perf_analyzer_repo
from app.schemas import CreateTritonInstanceRequest, TritonInstanceDTO, UpdateTritonInstanceRequest
from app.services.access import ensure_instance_access, get_instance_or_404
from app.services.deployment import kubernetes as deployment_k8s
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster
from app.services.triton.client import TritonService
from app.services.triton.repository_snapshot import normalize_repository_models, repository_model_names

logger = logging.getLogger(__name__)
_DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS = 5.0


async def create_instance(
    request: CreateTritonInstanceRequest,
    session: Session,
    claims: dict[str, Any],
) -> TritonInstanceDTO:
    require_member_or_admin(claims)

    try:
        triton_service = TritonService(
            request.url,
            request.verify_ssl,
            request.ca_certificate,
            timeout=triton_connection_validation_timeout_seconds(),
        )
    except RuntimeError as exc:
        raise BadRequestError(str(exc)) from exc

    validation_timeout = triton_connection_validation_timeout_seconds()
    runtime_snapshot = await _collect_runtime_snapshot_with_timeout(
        triton_service,
        request.url,
        validation_timeout,
    )
    if not runtime_snapshot["ready"]:
        raise ServiceUnavailableError(
            _format_triton_unavailable_detail(request.url, runtime_snapshot["error"]),
        )

    metrics_snapshot = await triton_service.collect_metrics_snapshot(request.metrics_url)
    repository_models = normalize_repository_models(await triton_service.get_repository_index())
    model_names = repository_model_names(repository_models)
    instance_name = (request.name or request.url).strip()

    existing_by_url = instances.find_by_url(session, request.url)
    existing_by_name = instances.find_by_name(session, instance_name)

    user = require_user_entity(session, claims)

    if existing_by_url:
        if existing_by_name and existing_by_name.id != existing_by_url.id:
            raise ConflictError(
                f"Instance name '{instance_name}' already exists",
            )

        existing_by_url.name = instance_name
        existing_by_url.model_names = model_names
        existing_by_url.repository_models = repository_models
        existing_by_url.server_metadata = runtime_snapshot["metadata"]
        existing_by_url.health_live = runtime_snapshot["live"]
        existing_by_url.health_ready = runtime_snapshot["ready"]
        existing_by_url.health_last_checked_at = runtime_snapshot["checked_at"]
        existing_by_url.health_error = runtime_snapshot["error"]
        existing_by_url.triton_verify_ssl = request.verify_ssl
        existing_by_url.triton_ca_certificate = request.ca_certificate if request.verify_ssl else ""
        existing_by_url.metrics_url = request.metrics_url
        existing_by_url.metrics_cpu = metrics_snapshot["cpu"]
        existing_by_url.metrics_ram = metrics_snapshot["ram"]
        existing_by_url.metrics_gpu = metrics_snapshot["gpu"]
        existing_by_url.metrics_last_checked_at = metrics_snapshot["checked_at"]
        existing_by_url.metrics_error = metrics_snapshot["error"]
        if existing_by_url.created_by_user_id is None:
            existing_by_url.created_by_user_id = user.id
        instances.save(session, existing_by_url)

        if instance_name not in (user.assigned_instances or []):
            user.assigned_instances = [*user.assigned_instances, instance_name]
            users.save(session, user)

        return entity_to_dto(existing_by_url)

    if existing_by_name:
        raise ConflictError(
            f"Instance name '{instance_name}' already exists",
        )

    entity = instances.create(
        session,
        url=request.url,
        name=instance_name,
        model_names=model_names,
        repository_models=repository_models,
        server_metadata=runtime_snapshot["metadata"],
        health_live=runtime_snapshot["live"],
        health_ready=runtime_snapshot["ready"],
        health_last_checked_at=runtime_snapshot["checked_at"],
        health_error=runtime_snapshot["error"],
        triton_verify_ssl=request.verify_ssl,
        triton_ca_certificate=request.ca_certificate if request.verify_ssl else "",
        metrics_url=request.metrics_url,
        metrics_cpu=metrics_snapshot["cpu"],
        metrics_ram=metrics_snapshot["ram"],
        metrics_gpu=metrics_snapshot["gpu"],
        metrics_last_checked_at=metrics_snapshot["checked_at"],
        metrics_error=metrics_snapshot["error"],
        created_by_user_id=user.id,
    )

    if instance_name not in (user.assigned_instances or []):
        user.assigned_instances = [*user.assigned_instances, instance_name]
        users.save(session, user)

    return entity_to_dto(entity)


async def update_instance(
    request: UpdateTritonInstanceRequest,
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
) -> TritonInstanceDTO:
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)

    existing_by_url = instances.find_by_url(session, request.url)
    if existing_by_url and existing_by_url.id != instance.id:
        raise ConflictError(f"Instance URL '{request.url}' already exists")

    try:
        triton_service = TritonService(
            request.url,
            request.verify_ssl,
            request.ca_certificate,
            timeout=triton_connection_validation_timeout_seconds(),
        )
    except RuntimeError as exc:
        raise BadRequestError(str(exc)) from exc

    validation_timeout = triton_connection_validation_timeout_seconds()
    runtime_snapshot = await _collect_runtime_snapshot_with_timeout(
        triton_service,
        request.url,
        validation_timeout,
    )
    if not runtime_snapshot["ready"]:
        raise ServiceUnavailableError(
            _format_triton_unavailable_detail(request.url, runtime_snapshot["error"]),
        )

    metrics_snapshot = await triton_service.collect_metrics_snapshot(request.metrics_url)
    repository_models = normalize_repository_models(await triton_service.get_repository_index())

    instance.url = request.url
    instance.model_names = repository_model_names(repository_models)
    instance.repository_models = repository_models
    instance.server_metadata = runtime_snapshot["metadata"]
    instance.health_live = runtime_snapshot["live"]
    instance.health_ready = runtime_snapshot["ready"]
    instance.health_last_checked_at = runtime_snapshot["checked_at"]
    instance.health_error = runtime_snapshot["error"]
    instance.triton_verify_ssl = request.verify_ssl
    instance.triton_ca_certificate = request.ca_certificate if request.verify_ssl else ""
    instance.metrics_url = request.metrics_url
    instance.metrics_cpu = metrics_snapshot["cpu"]
    instance.metrics_ram = metrics_snapshot["ram"]
    instance.metrics_gpu = metrics_snapshot["gpu"]
    instance.metrics_last_checked_at = metrics_snapshot["checked_at"]
    instance.metrics_error = metrics_snapshot["error"]

    instances.save(session, instance)
    return entity_to_dto(instance)


def list_instances(session: Session, claims: dict[str, Any], limit: int = 100) -> list[TritonInstanceDTO]:
    assigned: list[str] | None = None
    if not is_admin(claims):
        user = require_user_entity(session, claims)
        assigned = [name for name in (user.assigned_instances or []) if name]

    rows = instances.list_visible(session, limit=limit, assigned_names=assigned)
    return [entity_to_dto(row) for row in rows]


def get_instance(session: Session, claims: dict[str, Any], instance_id: int) -> TritonInstanceDTO:
    instance = get_instance_or_404(session, instance_id, claims)
    return entity_to_dto(instance)


def delete_instance(session: Session, claims: dict[str, Any], instance_id: int) -> None:
    require_admin(claims)
    instance = instances.find_by_id(session, instance_id)
    if not instance:
        raise NotFoundError("Instance not found")

    instance_name = instance.name
    deployment_namespace = (instance.deployment_namespace or "").strip()
    if instance.is_self_deployed and deployment_namespace:
        try:
            control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
            if control_ns and deployment_namespace == control_ns:
                deployment_k8s.delete_deployment_resources(
                    namespace=deployment_namespace,
                    deployment_name=(instance.deployment_name or "").strip(),
                    service_name=(instance.deployment_service_name or "").strip(),
                    secret_name=(instance.deployment_secret_name or "").strip(),
                )
            else:
                deployment_k8s.delete_namespace(deployment_namespace)
        except Exception as exc:
            logger.warning(
                "Namespace cleanup failed for self-deployed instance id=%s namespace=%s: %s",
                instance.id,
                deployment_namespace,
                exc,
            )
            raise BadRequestError(str(exc)) from exc

    for user in users.list_all(session):
        current = user.assigned_instances or []
        next_assigned = [name for name in current if name != instance_name]
        if next_assigned != current:
            user.assigned_instances = next_assigned
            session.add(user)

    if instance.id is None:
        raise NotFoundError("Instance not found")

    perf_analyzer_repo.delete_runs_for_instance(session, instance.id)
    dashboard_alerts.delete_for_instance(session, instance.id, instance_name)
    instances.delete(session, instance)


def get_instance_by_name(session: Session, claims: dict[str, Any], name: str) -> TritonInstanceDTO:
    instance = instances.find_by_name(session, name)
    if not instance:
        raise NotFoundError("Instance not found")
    ensure_instance_access(session, claims, instance.name)
    return entity_to_dto(instance)


def _format_triton_unavailable_detail(url: str, error: Any) -> str:
    detail = f"Triton server at {url} is not ready"
    if isinstance(error, str) and error.strip():
        return f"{detail}: {error.strip()}"
    return detail


async def _collect_runtime_snapshot_with_timeout(
    triton_service: TritonService,
    url: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(
            triton_service.collect_runtime_snapshot(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        return {
            "metadata": None,
            "live": False,
            "ready": False,
            "checked_at": datetime.now(timezone.utc),
            "error": f"validation timed out after {timeout_seconds:g} seconds for {url}",
        }


def triton_connection_validation_timeout_seconds() -> float:
    raw = (os.getenv("TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS=%r; using default %.1f",
            raw,
            _DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS,
        )
        return _DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS
    if value <= 0:
        logger.warning(
            "TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS must be positive; using default %.1f",
            _DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS,
        )
        return _DEFAULT_TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS
    return value
