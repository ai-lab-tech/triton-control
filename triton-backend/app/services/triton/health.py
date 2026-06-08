"""Background health-monitoring task for registered Triton instances.

Defines ``InstanceHealthRefresher``, an asyncio background task that:
  - Polls every registered ``TritonInstanceEntity`` at a configurable interval.
  - Checks liveness (``/v2/health/live``) and readiness (``/v2/health/ready``).
  - Updates health fields (``is_live``, ``is_ready``, ``last_health_check``) in
    the database after each cycle.
  - Rebuilds the ``DashboardAlertEntity`` table so the dashboard always reflects
    the latest health snapshot.
  - Optionally probes the S3 configuration when S3 credentials are present.

A module-level singleton ``instance_health_refresher`` is started by the
FastAPI application on startup and stopped on shutdown.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.orm.exc import StaleDataError

from app.core.logging import get_logger
from app.db.database import session_factory
from app.db.entities import DashboardAlertEntity, TritonInstanceEntity
from app.repositories import dashboard_alerts, instances
from app.services.deployment import kubernetes as deployment_k8s
from app.services.storage.s3_client import build_s3_client, format_s3_error, is_s3_configured
from app.services.triton.client import TritonService
from app.services.triton.repository_snapshot import (
    normalize_repository_models,
    repository_model_names,
    unavailable_repository_model_count,
)

logger = get_logger(__name__)
DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS = 10
DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS = 5.0


def _should_refresh_server_metadata(server_metadata: object) -> bool:
    if server_metadata is None:
        return True
    if not isinstance(server_metadata, dict):
        return False

    deployment_status = str(server_metadata.get("deployment_status") or "").strip().lower()
    if deployment_status in {"deploying", "starting", "pending"}:
        return True
    return False


def health_refresh_interval_seconds() -> int:
    raw_value = os.getenv("TRITON_HEALTH_REFRESH_INTERVAL_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS
    try:
        interval = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid TRITON_HEALTH_REFRESH_INTERVAL_SECONDS=%r; using default %s",
            raw_value,
            DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS,
        )
        return DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS
    if interval <= 0:
        logger.warning(
            "TRITON_HEALTH_REFRESH_INTERVAL_SECONDS must be greater than 0; using default %s",
            DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS,
        )
        return DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS
    return interval


def health_request_timeout_seconds() -> float:
    raw_value = os.getenv("TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except ValueError:
        logger.warning(
            "Invalid TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS=%r; using default %.1f",
            raw_value,
            DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS,
        )
        return DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS
    if timeout <= 0:
        logger.warning(
            "TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS must be greater than 0; using default %.1f",
            DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS,
        )
        return DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS
    return timeout


class InstanceHealthRefresher:
    def __init__(self, interval_seconds: int = 10):
        self.interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._refresh_all_instances()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Periodic Triton health refresh failed")

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                continue

    async def _refresh_all_instances(self) -> None:
        with session_factory() as session:
            rows = instances.list_ids(session)

        alerts: list[DashboardAlertEntity] = []
        for instance_id in rows:
            if instance_id is None:
                continue
            alerts.extend(await self._refresh_instance(instance_id))

        with session_factory() as session:
            dashboard_alerts.replace_all(session, alerts)

    async def _refresh_instance(self, instance_id: int) -> list[DashboardAlertEntity]:
        with session_factory() as session:
            instance = instances.find_by_id(session, instance_id)
            if not instance:
                return []

            include_metadata = _should_refresh_server_metadata(instance.server_metadata)
            target_url = instance.url
            target_metrics_url = instance.metrics_url
            is_self_deployed = instance.is_self_deployed
            deployment_namespace = instance.deployment_namespace
            deployment_name = instance.deployment_name or ""
            deployment_service_name = instance.deployment_service_name or deployment_name
            triton_verify_ssl = instance.triton_verify_ssl
            triton_ca_certificate = instance.triton_ca_certificate

        resolved_urls: dict[str, str] | None = None
        pod_statuses = instance.pod_statuses
        if is_self_deployed and deployment_namespace:
            ns = deployment_namespace
            dn = deployment_name
            pod_statuses = await asyncio.to_thread(deployment_k8s.read_pod_statuses, ns, dn)
            if not await asyncio.to_thread(deployment_k8s.is_pod_ready, ns, dn):
                with session_factory() as session:
                    current = instances.find_by_id(session, instance_id)
                    if not current:
                        return []
                    current.pod_statuses = pod_statuses
                    current.health_live = False
                    current.health_ready = False
                    current.health_error = "Waiting for pod to become ready..."
                    current.model_names = []
                    current.repository_models = []
                    try:
                        instances.save(session, current)
                    except StaleDataError:
                        pass
                return []
            if not (target_url or "").strip():
                resolved_urls = await asyncio.to_thread(
                    deployment_k8s.resolve_deployment_service_urls,
                    ns,
                    deployment_service_name,
                )
                target_url = resolved_urls["http"]
                if not (target_metrics_url or "").strip():
                    target_metrics_url = resolved_urls["metrics"]

        triton_service = TritonService(
            target_url,
            triton_verify_ssl,
            triton_ca_certificate,
            timeout=health_request_timeout_seconds(),
        )
        snapshot = await triton_service.collect_runtime_snapshot(include_metadata=include_metadata)
        metrics_snapshot = None
        if target_metrics_url:
            metrics_snapshot = await triton_service.collect_metrics_snapshot(target_metrics_url)
        repository_models = []
        if snapshot["live"] and snapshot["ready"]:
            try:
                repository_models = normalize_repository_models(await triton_service.get_repository_index())
            except Exception:
                logger.exception("Failed to refresh repository index for instance %s", instance.name)

        with session_factory() as session:
            current = instances.find_by_id(session, instance_id)
            if not current:
                return []
            if resolved_urls and not (current.url or "").strip():
                current.url = resolved_urls["http"]
                if not (current.metrics_url or "").strip():
                    current.metrics_url = resolved_urls["metrics"]
            if include_metadata:
                current.server_metadata = snapshot["metadata"]
            current.pod_statuses = pod_statuses
            current.health_live = snapshot["live"]
            current.health_ready = snapshot["ready"]
            current.health_last_checked_at = snapshot["checked_at"]
            current.health_error = snapshot["error"]
            current.repository_models = repository_models
            current.model_names = repository_model_names(repository_models)
            if metrics_snapshot is not None:
                current.metrics_cpu = metrics_snapshot["cpu"]
                current.metrics_ram = metrics_snapshot["ram"]
                current.metrics_gpu = metrics_snapshot["gpu"]
                current.metrics_last_checked_at = metrics_snapshot["checked_at"]
                current.metrics_error = metrics_snapshot["error"]
            try:
                instances.save(session, current)
            except StaleDataError:
                logger.info("Skipped health update for deleted instance id=%s", instance_id)
                return []
            instance = current

        alerts: list[DashboardAlertEntity] = []
        if not snapshot["live"] or not snapshot["ready"]:
            alerts.append(
                DashboardAlertEntity(
                    instance_id=instance.id,
                    instance_name=instance.name,
                    icon="warning",
                    label=f"Triton instance {instance.name} not healthy",
                    tone="down",
                )
            )

        unavailable_count = unavailable_repository_model_count(instance.repository_models)
        if unavailable_count > 0:
            alerts.append(
                DashboardAlertEntity(
                    instance_id=instance.id,
                    instance_name=instance.name,
                    icon="sync_problem",
                    label=(
                        f"{unavailable_count} model"
                        f"{'s' if unavailable_count != 1 else ''} unavailable on {instance.name}"
                    ),
                    tone="warn",
                )
            )

        if is_s3_configured(instance):
            try:
                await asyncio.to_thread(_probe_s3_connection, instance)
            except (BotoCoreError, ClientError, RuntimeError) as exc:
                error = format_s3_error(exc) if isinstance(exc, (BotoCoreError, ClientError)) else str(exc)
                logger.warning(
                    "Failed to reach configured S3 connection for instance %s: %s",
                    instance.name,
                    error,
                )
                alerts.append(
                    DashboardAlertEntity(
                        instance_id=instance.id,
                        instance_name=instance.name,
                        icon="cloud_off",
                        label=f"Configured S3 connection for {instance.name} not reachable: {error}",
                        tone="warn",
                    )
                )
            except Exception:
                logger.exception("Failed to reach configured S3 connection for instance %s", instance.name)
                alerts.append(
                    DashboardAlertEntity(
                        instance_id=instance.id,
                        instance_name=instance.name,
                        icon="cloud_off",
                        label=f"Configured S3 connection for {instance.name} not reachable",
                        tone="warn",
                    )
                )

        return alerts


def _probe_s3_connection(instance: TritonInstanceEntity) -> None:
    client = build_s3_client(instance)
    effective_prefix = (instance.s3_prefix or "").lstrip("/")
    client.list_objects_v2(
        Bucket=instance.s3_bucket,
        Prefix=effective_prefix,
        Delimiter="/",
        MaxKeys=1,
    )


instance_health_refresher = InstanceHealthRefresher(interval_seconds=health_refresh_interval_seconds())
