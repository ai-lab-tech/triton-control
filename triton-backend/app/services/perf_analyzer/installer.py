"""Benchmark command/input compatibility and saved legacy results."""

import json
import os
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from sqlmodel import Session

from app.exceptions import BadRequestError
from app.repositories import perf_analyzer
from app.schemas import PerfAnalyzerLatestRunResponse, RunPerfAnalyzerRequest
from app.services.access import get_instance_or_404
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster

_DEFAULT_NAMESPACE = "triton-control"


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


def _perf_analyzer_namespace() -> str:
    """Return the shared Triton Control namespace for Perf Analyzer."""
    control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
    if control_ns:
        return control_ns
    configured = (
        os.getenv("TRITON_CONTROL_NAMESPACE")
        or os.getenv("KUBERNETES_NAMESPACE")
        or os.getenv("POD_NAMESPACE")
        or ""
    ).strip()
    return configured or _DEFAULT_NAMESPACE


def _run_command(
    request: RunPerfAnalyzerRequest,
    instance: Any,
    *,
    perf_analyzer_namespace: str = "",
    input_data_arg: str | None = None,
    decoupled: bool | None = None,
) -> list[str]:
    target = _perf_analyzer_target(instance, perf_analyzer_namespace=perf_analyzer_namespace)
    protocol = _perf_analyzer_protocol(instance, target=target)
    if decoupled is None:
        decoupled = _requires_decoupled_perf_analyzer_mode(
            instance,
            model_name=request.model_name,
            model_version=request.model_version,
        )
    if decoupled:
        target = _grpc_perf_analyzer_target(target)
        protocol = "grpc"
    batch_size = _effective_perf_analyzer_batch_size(request, decoupled=decoupled)
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
        str(batch_size),
        "--concurrency-range",
        request.concurrency_range,
        "--measurement-mode",
        "count_windows",
        "--measurement-request-count",
        str(request.measurement_request_count),
    ]
    if decoupled:
        cmd.extend(["--async", "--streaming"])
    if input_data_arg:
        cmd.extend(["--input-data", input_data_arg])
    return cmd


def _effective_perf_analyzer_batch_size(request: RunPerfAnalyzerRequest, *, decoupled: bool) -> int:
    if decoupled:
        return 1
    return request.batch_size


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


def _requires_decoupled_perf_analyzer_mode(
    instance: Any,
    *,
    model_name: str | None = None,
    model_version: str | None = None,
) -> bool:
    """Return true for vLLM-backed Triton deployments.

    The Triton vLLM backend exposes decoupled models. Perf Analyzer requires
    decoupled models to run with async streaming over gRPC.
    """
    config = _fetch_triton_model_config(instance, model_name=model_name, model_version=model_version)
    if _model_config_requires_decoupled_perf_analyzer_mode(config):
        return True

    parts: list[str] = []
    for attr in ("deployment_log", "url"):
        parts.append(str(getattr(instance, attr, "") or ""))
    metadata = getattr(instance, "server_metadata", None)
    if isinstance(metadata, dict):
        parts.extend(str(value) for value in metadata.values())
    for model in getattr(instance, "repository_models", None) or []:
        if isinstance(model, dict):
            parts.extend(str(value) for value in model.values())
    return "vllm" in " ".join(parts).lower()


def _fetch_triton_model_config(
    instance: Any,
    *,
    model_name: str | None,
    model_version: str | None,
) -> dict[str, Any] | None:
    if not model_name:
        return None
    raw_url = str(getattr(instance, "url", "") or "").strip()
    if not raw_url:
        return None

    split = urlsplit(raw_url if "://" in raw_url else f"http://{raw_url}")
    scheme = "https" if split.scheme in {"https", "grpcs"} else "http"
    netloc = split.netloc or split.path
    host, sep, port = netloc.rpartition(":")
    if sep and port in {"18001", "8001"}:
        netloc = f"{host}:{'18000' if port == '18001' else '8000'}"
    version_path = ""
    if model_version:
        version_path = f"/versions/{quote(model_version, safe='')}"
    path = f"/v2/models/{quote(model_name, safe='')}{version_path}/config"
    config_url = urlunsplit((scheme, netloc, path, "", ""))
    try:
        with httpx.Client(timeout=5, follow_redirects=False, trust_env=False) as client:
            response = client.get(config_url)
            response.raise_for_status()
            payload = response.text
    except (httpx.HTTPError, ValueError):
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _model_config_requires_decoupled_perf_analyzer_mode(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    backend = str(config.get("backend") or "").strip().lower()
    if backend == "vllm":
        return True
    policy = config.get("model_transaction_policy")
    if isinstance(policy, dict) and policy.get("decoupled") is True:
        return True
    return False


def _grpc_perf_analyzer_target(target: str) -> str:
    host, sep, port = target.rpartition(":")
    if not sep:
        return target
    if port == "18000":
        return f"{host}:18001"
    if port == "8000":
        return f"{host}:8001"
    return target


def _prepare_input_data_for_perf_analyzer(input_data: str | None, *, decoupled: bool = False) -> str | None:
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

    if decoupled and isinstance(parsed, dict) and "text_input" in parsed:
        converted = _convert_vllm_generate_payload_to_perf_input(parsed)
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


def _convert_vllm_generate_payload_to_perf_input(payload: dict[str, Any]) -> dict[str, Any]:
    text_input = payload.get("text_input")
    if isinstance(text_input, str):
        prompt = text_input
    elif isinstance(text_input, list) and len(text_input) == 1 and isinstance(text_input[0], str):
        prompt = text_input[0]
    else:
        raise BadRequestError("vLLM Perf Analyzer input must include a string 'text_input'")

    raw_parameters = payload.get("parameters")
    sampling_parameters = dict(raw_parameters) if isinstance(raw_parameters, dict) else {}
    sampling_parameters.pop("stream", None)

    return {
        "data": [
            {
                "text_input": [prompt],
                "stream": [True],
                "sampling_parameters": [json.dumps(sampling_parameters, ensure_ascii=False)],
            }
        ]
    }
