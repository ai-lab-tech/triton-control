"""Authorize instance access before reading workload state or container logs."""

from datetime import datetime, timezone
from threading import Lock
from typing import Any

from cachetools import TTLCache
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.exceptions import (
    AppError,
    BadGatewayError,
    BadRequestError,
    ConflictError,
    NotFoundError,
    UnprocessableEntityError,
)
from app.schemas.pods import InstancePods, PodLogs, PodLogStream
from app.services.access import get_instance_or_404
from app.services.deployment import pod_provider
from app.services.kubernetes_client import api_client

# Bounded per-worker throttle. Cluster-wide request quotas belong at the ingress.
_log_reads: TTLCache[tuple[str, int], bool] = TTLCache(maxsize=4096, ttl=5)
_log_lock = Lock()
MAX_STREAMS = 16


class LogRefreshLimited(AppError):
    status_code = 429


def _managed(instance: Any) -> bool:
    return bool(instance.is_self_deployed and instance.deployment_runtime == "kubernetes")


def _target(instance: Any) -> tuple[str, str]:
    if not _managed(instance):
        raise BadRequestError("External endpoints expose health only; Kubernetes pods and logs are unavailable")
    if not instance.deployment_namespace or not instance.deployment_name:
        raise ConflictError("Deployment identity is not available yet")
    return instance.deployment_namespace, instance.deployment_name


def get_pods(session: Session, claims: dict[str, Any], instance_id: int) -> InstancePods:
    instance = get_instance_or_404(session, instance_id, claims)
    result = InstancePods(
        managed=_managed(instance),
        endpoint_live=instance.health_live,
        endpoint_ready=instance.health_ready,
        endpoint_checked_at=instance.health_last_checked_at,
    )
    if not _managed(instance):
        result.message = "External endpoint: Kubernetes pod state is unavailable"
        return result
    namespace, name = _target(instance)
    result.deployment, result.pods = pod_provider.read_snapshot(namespace, name)
    return result


def get_logs(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    *,
    pod_uid: str | None,
    container: str | None,
    previous: bool,
    since: datetime | None,
    lines: int,
    byte_limit: int,
) -> PodLogs:
    instance = get_instance_or_404(session, instance_id, claims)
    namespace, name = _target(instance)
    if since and (since.tzinfo is None or since > datetime.now(timezone.utc)):
        raise UnprocessableEntityError("since must be a timezone-aware timestamp in the past")
    key = (str(claims.get("sub") or claims.get("email") or ""), instance_id)
    with _log_lock:
        if key in _log_reads:
            raise LogRefreshLimited("Wait at least 5 seconds between log refreshes")
        _log_reads[key] = True
    try:
        with api_client() as api:
            provider = pod_provider.PodProvider(api)
            deployment, pods = provider.snapshot(namespace, name)
            if pod_uid:
                pods = [p for p in pods if p.uid == pod_uid]
                if not pods:
                    raise NotFoundError(
                        "Selected pod was removed or is not owned by this instance; select a current pod"
                    )
            if container and pod_uid and not any(c.name == container for c in pods[0].containers):
                raise NotFoundError("Container does not belong to the selected pod")
            targets = [(p, c) for p in pods for c in p.containers if not container or c.name == container]
            if container and not targets:
                raise NotFoundError("Container does not belong to any current owned pod")
            result = PodLogs(truncated=len(targets) > MAX_STREAMS)
            remaining = byte_limit
            for pod, state in targets[:MAX_STREAMS]:
                entry = PodLogStream(pod_uid=pod.uid, pod_name=pod.name, container=state.name, previous=previous)
                if remaining <= 0:
                    entry.truncated = True
                    entry.error = "Response byte limit reached; select a single pod/container"
                else:
                    try:
                        entry.text, entry.truncated = provider.logs(
                            namespace,
                            name,
                            deployment.uid,
                            pod,
                            state.name,
                            previous,
                            since,
                            lines,
                            remaining,
                        )
                        remaining -= len(entry.text.encode("utf-8"))
                    except NotFoundError as exc:
                        entry.error = exc.detail
                    except ApiException as exc:
                        entry.error = {
                            400: "Logs are unavailable (container has not started or has no previous instance)",
                            403: "Kubernetes denied access to these container logs",
                            404: "Pod or container logs no longer exist; refresh pod state",
                        }.get(exc.status, "Kubernetes could not read this container's logs")
                    except Exception:
                        entry.error = "Could not read this container's logs"
                result.truncated |= entry.truncated
                result.streams.append(entry)
            return result
    except AppError:
        raise
    except Exception as exc:
        raise BadGatewayError("Could not resolve owned Kubernetes pods for logs") from exc
