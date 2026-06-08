"""Use cases for the singleton Triton Perf Analyzer SDK container on Kubernetes.

Public surface: ``get_perf_analyzer_status``, ``install_perf_analyzer``,
``uninstall_perf_analyzer``, and ``run_perf_analyzer``.
The service coordinates the installation record and delegates Kubernetes
manifest application to the Kubernetes integration.
"""

import json
from datetime import datetime
from threading import Lock
from typing import Any

from sqlmodel import Session

from app.db.entities import PerfAnalyzerEntity, PerfAnalyzerRunEntity
from app.exceptions import BadRequestError, ConflictError
from app.mappers import perf_analyzer_entity_to_dto
from app.repositories import perf_analyzer
from app.schemas import (
    InstallPerfAnalyzerRequest,
    PerfAnalyzerDeleteResponse,
    PerfAnalyzerInstallResponse,
    PerfAnalyzerLatestRunResponse,
    PerfAnalyzerRunResponse,
    PerfAnalyzerStatusResponse,
    RunPerfAnalyzerRequest,
)
from app.services.access import get_instance_or_404
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster
from app.services.perf_analyzer import kubernetes as k8s

_run_lock = Lock()
_install_lock = Lock()


def get_perf_analyzer_status(session: Session) -> PerfAnalyzerStatusResponse:
    """Return the persisted singleton Perf Analyzer installation state."""
    entity = perf_analyzer.get(session)
    if entity is None:
        return PerfAnalyzerStatusResponse(
            installed=False,
            status="not_installed",
            ready=False,
            status_message="",
        )
    ready, message = k8s.read_installation_readiness(entity.namespace)
    status = (entity.status or "").strip() or "creating"
    if status == "ready" and not ready:
        status = "creating"
    return PerfAnalyzerStatusResponse(
        installed=True,
        status=status,
        ready=ready,
        status_message=message or entity.status_message,
        installation=perf_analyzer_entity_to_dto(entity),
    )


def get_latest_perf_analyzer_run(
    request: RunPerfAnalyzerRequest,
    session: Session,
    claims: dict[str, Any],
) -> PerfAnalyzerLatestRunResponse:
    """Return the persisted latest Perf Analyzer result for one model target."""
    get_instance_or_404(session, request.instance_id, claims)
    entity = perf_analyzer.get_latest_run(
        session,
        instance_id=request.instance_id,
        model_name=request.model_name,
        model_version=request.model_version,
    )
    if entity is None:
        return PerfAnalyzerLatestRunResponse(found=False)
    return PerfAnalyzerLatestRunResponse(
        found=True,
        executed_at=entity.executed_at,
        batch_size=entity.batch_size,
        concurrency_range=entity.concurrency_range,
        measurement_request_count=entity.measurement_request_count,
        input_data=entity.input_data,
        command=entity.command,
        output=entity.output,
    )


def install_perf_analyzer(request: InstallPerfAnalyzerRequest, session: Session) -> PerfAnalyzerInstallResponse:
    """Install Perf Analyzer resources and return the applied Kubernetes names."""
    if not _install_lock.acquire(blocking=False):
        raise ConflictError("Perf Analyzer installation is already in progress")

    try:
        if perf_analyzer.get(session) is not None:
            raise ConflictError("Perf Analyzer is already installed")
        name = request.installation_name
        control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
        namespace = control_ns or name
        entity = perf_analyzer.save(
            session,
            PerfAnalyzerEntity(
                namespace=namespace,
                deployment_name=name,
                image=request.image,
                applied_resources=[],
                status="creating",
                status_message="Creating Kubernetes resources and waiting for pod readiness.",
                last_transition_at=datetime.utcnow(),
            ),
        )
        try:
            applied = k8s.apply_installation_resources(request, namespace=namespace, deployment_name=name)
            entity.applied_resources = applied
            entity.status = "ready"
            entity.status_message = "Perf Analyzer pod is Running."
            entity.last_transition_at = datetime.utcnow()
            entity = perf_analyzer.save(session, entity)
            return perf_analyzer_entity_to_dto(entity)
        except Exception:
            # Mark failure before cleanup, then remove placeholder singleton row so retries are possible.
            entity.status = "failed"
            entity.status_message = "Perf Analyzer installation failed."
            entity.last_transition_at = datetime.utcnow()
            perf_analyzer.save(session, entity)
            perf_analyzer.delete(session, entity)
            raise
    finally:
        _install_lock.release()


def uninstall_perf_analyzer(session: Session) -> PerfAnalyzerDeleteResponse:
    """Delete the Perf Analyzer namespace and clear its singleton record."""
    entity = perf_analyzer.get(session)
    if entity is None:
        raise BadRequestError("Perf Analyzer is not installed")
    namespace = entity.namespace
    entity.status = "deleting"
    entity.status_message = "Deleting Perf Analyzer resources."
    entity.last_transition_at = datetime.utcnow()
    perf_analyzer.save(session, entity)
    control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
    if control_ns and namespace == control_ns:
        message = k8s.delete_installation_resources(namespace, entity.deployment_name)
    else:
        message = k8s.delete_namespace(namespace)
    perf_analyzer.delete(session, entity)
    return PerfAnalyzerDeleteResponse(status="deleted", message=message, namespace=namespace)


def run_perf_analyzer(
    request: RunPerfAnalyzerRequest,
    session: Session,
    claims: dict[str, Any],
) -> PerfAnalyzerRunResponse:
    """Run Perf Analyzer in the installed SDK pod for one Triton model."""
    if not _run_lock.acquire(blocking=False):
        raise ConflictError("Another Perf Analyzer run is already in progress")

    try:
        return _run_perf_analyzer_locked(request, session, claims)
    finally:
        _run_lock.release()


def _run_perf_analyzer_locked(
    request: RunPerfAnalyzerRequest,
    session: Session,
    claims: dict[str, Any],
) -> PerfAnalyzerRunResponse:
    entity = perf_analyzer.get(session)
    if entity is None:
        raise BadRequestError("Perf Analyzer is not installed")
    instance = get_instance_or_404(session, request.instance_id, claims)
    prepared_input_data = _prepare_input_data_for_perf_analyzer(request.input_data)
    input_data_arg: str | None = None
    if prepared_input_data:
        direct_input = _direct_perf_input_argument(prepared_input_data)
        if direct_input is not None:
            input_data_arg = direct_input
        else:
            # Path is inside the ephemeral Perf Analyzer pod, not on the backend host.
            # /tmp can be read-only under restricted PodSecurityContext; /dev/shm
            # is writable in our deployment baseline.
            input_data_arg = "/dev/shm/pa_input.json"  # nosec B108
            k8s.write_file_to_pod(entity.namespace, input_data_arg, prepared_input_data)
    command = _run_command(
        request,
        instance,
        perf_analyzer_namespace=entity.namespace,
        input_data_arg=input_data_arg,
    )
    output = k8s.exec_running_pod(entity.namespace, command)
    run = perf_analyzer.get_latest_run(
        session,
        instance_id=request.instance_id,
        model_name=request.model_name,
        model_version=request.model_version,
    )
    if run is None:
        run = PerfAnalyzerRunEntity(
            instance_id=request.instance_id,
            model_name=request.model_name,
            model_version=request.model_version,
        )
    run.batch_size = request.batch_size
    run.concurrency_range = request.concurrency_range
    run.measurement_request_count = request.measurement_request_count
    run.input_data = prepared_input_data
    run.command = command
    run.output = output
    run.executed_at = datetime.utcnow()
    perf_analyzer.save_latest_run(session, run)
    return PerfAnalyzerRunResponse(command=command, output=output)


def _run_command(
    request: RunPerfAnalyzerRequest,
    instance: Any,
    *,
    perf_analyzer_namespace: str = "",
    input_data_arg: str | None = None,
) -> list[str]:
    target = _perf_analyzer_target(instance, perf_analyzer_namespace=perf_analyzer_namespace)
    protocol = _perf_analyzer_protocol(instance, target=target)
    cmd = [
        "perf_analyzer",
        "-m",
        request.model_name,
        "-x",
        request.model_version,
        "-u",
        target,
        "-i",
        protocol,
        "-b",
        str(request.batch_size),
        "--concurrency-range",
        request.concurrency_range,
        "--measurement-mode",
        "count_windows",
        "--measurement-request-count",
        str(request.measurement_request_count),
    ]
    if input_data_arg:
        cmd.extend(["--input-data", input_data_arg])
    return cmd


def _perf_analyzer_target(instance: Any, *, perf_analyzer_namespace: str = "") -> str:
    instance_url = getattr(instance, "url", "")
    service_name = (getattr(instance, "deployment_service_name", None) or "").strip()
    namespace = (getattr(instance, "deployment_namespace", None) or "").strip()
    same_namespace = bool(perf_analyzer_namespace and namespace and perf_analyzer_namespace == namespace)
    if service_name and namespace and same_namespace and _is_internal_service_url(
        instance_url,
        service_name=service_name,
        namespace=namespace,
    ):
        # Prefer the exact saved service URL host:port. This avoids synthesizing
        # a possibly different service DNS name from deployment metadata.
        target = _external_perf_analyzer_target(instance_url)
        if target:
            return target
        return f"{service_name}.{namespace}.svc.cluster.local:18000"
    return _external_perf_analyzer_target(instance_url)


def _external_perf_analyzer_target(triton_url: str) -> str:
    from urllib.parse import urlsplit

    split = urlsplit(triton_url if "://" in triton_url else f"http://{triton_url}")
    target = (split.netloc or split.path).strip("/")
    if not target:
        raise BadRequestError("Triton URL cannot be used by Perf Analyzer")
    return target


def _is_internal_service_url(url: Any, *, service_name: str, namespace: str) -> bool:
    raw_url = str(url or "").strip()
    if not raw_url:
        return True

    from urllib.parse import urlsplit

    split = urlsplit(raw_url if "://" in raw_url else f"http://{raw_url}")
    host = (split.hostname or "").strip().lower()
    if not host:
        return True

    expected = {
        f"{service_name}.{namespace}.svc.cluster.local",
        f"{service_name}.{namespace}.svc",
        service_name,
        "localhost",
        "127.0.0.1",
    }
    return host in expected or host.endswith(".svc.cluster.local")


def _perf_analyzer_protocol(instance: Any, *, target: str) -> str:
    if target.endswith(".svc.cluster.local:18000") or target.endswith(".svc:18000"):
        return "HTTP"

    from urllib.parse import urlsplit

    raw_url = str(getattr(instance, "url", "") or "").strip()
    split = urlsplit(raw_url if "://" in raw_url else f"http://{raw_url}")
    scheme = (split.scheme or "").strip().lower()
    if scheme in {"grpc", "grpcs"}:
        return "gRPC"
    return "HTTP"


def _prepare_input_data_for_perf_analyzer(input_data: str | None) -> str | None:
    if input_data is None:
        return None

    raw = input_data.strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Keep non-JSON payloads as-is; downstream tooling will validate.
        return input_data

    if isinstance(parsed, dict) and isinstance(parsed.get("data"), list):
        # Already in perf_analyzer JSON format.
        return input_data

    if isinstance(parsed, dict) and isinstance(parsed.get("inputs"), list):
        converted = _convert_inference_payload_to_perf_input(parsed)
        return json.dumps(converted, ensure_ascii=False)

    return input_data


def _direct_perf_input_argument(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    lower = raw.lower()
    if lower in {"zero", "random"}:
        return lower
    if ("\n" not in raw) and ("{" not in raw) and ("[" not in raw) and ("/" in raw):
        # Caller passed a path (for example /tmp/input.json or /data directory).
        # Perf Analyzer can consume it directly via --input-data.
        return raw
    return None


def _convert_inference_payload_to_perf_input(payload: dict[str, Any]) -> dict[str, Any]:
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise BadRequestError("Inference payload must include a non-empty 'inputs' array")

    request_item: dict[str, Any] = {}
    for idx, item in enumerate(inputs):
        if not isinstance(item, dict):
            raise BadRequestError(f"inputs[{idx}] must be an object")
        name = item.get("name")
        data = item.get("data")
        if not isinstance(name, str) or not name.strip():
            raise BadRequestError(f"inputs[{idx}].name must be a non-empty string")
        if data is None:
            raise BadRequestError(f"inputs[{idx}].data is required for conversion")
        request_item[name] = data

    return {"data": [request_item]}
