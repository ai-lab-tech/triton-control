"""Business logic for Triton model repository operations.

Provides async use cases that proxy model commands to a live Triton instance:
  ``list_models(session, claims, instance_id)``           — return the latest
    persisted repository snapshot as ``TritonRepositoryModelDTO`` rows.
  ``get_model_config(session, claims, id, name, version)``— fetch, parse, and
    return the protobuf model config as JSON.
  ``load_model(session, claims, id, name)``               — trigger a model
    load in Triton.
  ``unload_model(session, claims, id, name)``             — trigger a model
    unload in Triton.
  ``infer_model(session, claims, id, name, version, req)``— proxy a raw
    inference request and return the Triton response verbatim.
"""

import asyncio
import base64
import json
import os
from typing import Any

from fastapi import Response
from sqlmodel import Session

from app.core.access_control import require_member_or_admin
from app.exceptions import BadGatewayError, BadRequestError, NotFoundError
from app.schemas import ModelRepositoryActionResponse, TritonRepositoryModelDTO
from app.services.access import get_instance_or_404
from app.services.triton.client import TritonService
from app.services.triton.config import extract_triton_error_detail

GENERATE_ENDPOINT_BACKENDS = {"vllm", "tensorrtllm", "tensorrt_llm", "trtllm"}
TENSORRT_LLM_GENERATE_BACKENDS = {"tensorrtllm", "tensorrt_llm", "trtllm"}
DEFAULT_INFERENCE_TIMEOUT_SECONDS = 600.0


async def list_models(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
) -> list[TritonRepositoryModelDTO]:
    """Return the latest persisted Triton model repository snapshot."""
    instance = get_instance_or_404(session, instance_id, claims)
    models: list[TritonRepositoryModelDTO] = []
    for row in instance.repository_models or []:
        name = row.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        version = row.get("version")
        state = row.get("state")
        reason = row.get("reason")
        models.append(
            TritonRepositoryModelDTO(
                name=name.strip(),
                version=str(version) if version is not None else None,
                state=str(state) if state is not None else None,
                reason=str(reason) if reason is not None else None,
            )
        )

    models.sort(key=lambda item: (item.name.lower(), item.version or ""))
    return models


async def get_model_config(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    model_name: str,
    version: str,
) -> Any:
    """Return the live Triton model config for a specific model version."""
    instance = get_instance_or_404(session, instance_id, claims)

    try:
        return await TritonService(
            instance.url,
            instance.triton_verify_ssl,
            instance.triton_ca_certificate,
        ).get_model_config(model_name, version)
    except Exception as exc:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 404:
            raise NotFoundError("Model config not found") from exc
        raise BadGatewayError("Failed to load model config from Triton") from exc


async def load_model(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    model_name: str,
) -> ModelRepositoryActionResponse:
    """Trigger explicit load for a model via Triton's repository API."""
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)

    try:
        await TritonService(
            instance.url,
            instance.triton_verify_ssl,
            instance.triton_ca_certificate,
        ).load_model(model_name)
        return ModelRepositoryActionResponse(status="ok", message="Model load requested.")
    except Exception as exc:
        raise BadRequestError(extract_triton_error_detail(exc)) from exc


async def unload_model(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    model_name: str,
) -> ModelRepositoryActionResponse:
    """Trigger explicit unload for a model via Triton's repository API."""
    require_member_or_admin(claims)
    instance = get_instance_or_404(session, instance_id, claims)

    try:
        await TritonService(
            instance.url,
            instance.triton_verify_ssl,
            instance.triton_ca_certificate,
        ).unload_model(model_name)
        return ModelRepositoryActionResponse(status="ok", message="Model unload requested.")
    except Exception as exc:
        raise BadRequestError(extract_triton_error_detail(exc)) from exc


async def infer_model(
    session: Session,
    claims: dict[str, Any],
    instance_id: int,
    model_name: str,
    version: str,
    payload_bytes: bytes,
    content_type: str,
) -> Response:
    """Proxy a Triton inference request for a specific model version."""
    if not payload_bytes:
        raise BadRequestError("Request body is required")

    instance = get_instance_or_404(session, instance_id, claims)
    triton_service = TritonService(
        instance.url,
        instance.triton_verify_ssl,
        instance.triton_ca_certificate,
        timeout=triton_inference_timeout_seconds(),
    )
    use_metrics = bool(instance.metrics_url)
    metrics_before = (
        await triton_service.collect_inference_metrics_snapshot(instance.metrics_url) if use_metrics else None
    )
    use_stats = not _is_metrics_snapshot_available(metrics_before)
    fallback_stats_before = None if use_stats else await triton_service.collect_inference_stats_snapshot()
    source = "stats" if use_stats else "metrics"
    stats_before = await triton_service.collect_inference_stats_snapshot() if use_stats else None
    try:
        if await _uses_generate_endpoint_backend(triton_service, model_name, version):
            triton_response = await triton_service.generate_model_raw(
                model_name,
                payload_bytes,
                content_type,
            )
        else:
            triton_response = await triton_service.infer_model_raw(
                model_name,
                version,
                payload_bytes,
                content_type,
            )
        if use_stats:
            stats_after = await triton_service.collect_inference_stats_snapshot()
            inference_metrics = triton_service.inference_metrics_delta(stats_before or {}, stats_after)
        else:
            metrics_after = await triton_service.collect_inference_metrics_snapshot(instance.metrics_url)
            inference_metrics = triton_service.inference_metrics_delta(metrics_before or {}, metrics_after)
        for delay_seconds in (0.1, 0.3, 0.75, 1.5):
            if inference_metrics.get("available"):
                break
            await asyncio.sleep(delay_seconds)
            if use_stats:
                stats_after = await triton_service.collect_inference_stats_snapshot()
                inference_metrics = triton_service.inference_metrics_delta(stats_before or {}, stats_after)
            else:
                metrics_after = await triton_service.collect_inference_metrics_snapshot(instance.metrics_url)
                inference_metrics = triton_service.inference_metrics_delta(metrics_before or {}, metrics_after)
        inference_metrics = _with_metric_context(
            inference_metrics,
            source,
            metrics_before if source == "metrics" else stats_before,
            metrics_after if source == "metrics" else stats_after,
        )
        if source == "metrics" and not inference_metrics.get("available"):
            stats_after = await triton_service.collect_inference_stats_snapshot()
            inference_metrics = triton_service.inference_metrics_delta(fallback_stats_before or {}, stats_after)
            for delay_seconds in (0.1, 0.3, 0.75):
                if inference_metrics.get("available"):
                    break
                await asyncio.sleep(delay_seconds)
                stats_after = await triton_service.collect_inference_stats_snapshot()
                inference_metrics = triton_service.inference_metrics_delta(fallback_stats_before or {}, stats_after)
            inference_metrics = _with_metric_context(
                inference_metrics,
                "stats",
                fallback_stats_before,
                stats_after,
            )
        media_type = triton_response.headers.get("content-type", "application/json")
        headers = {
            "X-Triton-Inference-Metrics": _encode_metrics_header(inference_metrics),
            "Access-Control-Expose-Headers": "X-Triton-Inference-Metrics",
        }
        return Response(
            content=triton_response.content,
            status_code=triton_response.status_code,
            media_type=media_type,
            headers=headers,
        )
    except Exception as exc:
        detail = extract_triton_error_detail(exc)
        raise BadGatewayError(detail) from exc


def _encode_metrics_header(metrics: dict[str, Any]) -> str:
    payload = json.dumps(metrics, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def triton_inference_timeout_seconds() -> float:
    raw_value = (os.getenv("TRITON_INFERENCE_TIMEOUT_SECONDS") or "").strip()
    if not raw_value:
        return DEFAULT_INFERENCE_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except ValueError:
        return DEFAULT_INFERENCE_TIMEOUT_SECONDS
    if timeout <= 0:
        return DEFAULT_INFERENCE_TIMEOUT_SECONDS
    return timeout


async def _uses_generate_endpoint_backend(
    triton_service: TritonService,
    model_name: str,
    version: str,
) -> bool:
    try:
        config = await triton_service.get_model_config(model_name, version)
    except Exception:
        return False

    return _model_config_uses_generate_endpoint(config)


def _model_config_uses_generate_endpoint(config: Any) -> bool:
    parts: list[str] = []
    if isinstance(config, dict):
        for key in ("backend", "platform"):
            value = config.get(key)
            if value is not None:
                parts.append(str(value))
        parameters = config.get("parameters")
        if isinstance(parameters, dict):
            for value in parameters.values():
                if isinstance(value, dict):
                    string_value = value.get("string_value")
                    if string_value is not None:
                        parts.append(str(string_value))
                elif value is not None:
                    parts.append(str(value))
    normalized_parts = {_normalize_backend(part) for part in parts}
    if "vllm" in normalized_parts:
        return True
    if normalized_parts & TENSORRT_LLM_GENERATE_BACKENDS:
        return _model_config_has_input(config, "text_input")
    return False


def _model_config_has_input(config: Any, input_name: str) -> bool:
    if not isinstance(config, dict):
        return False
    for item in config.get("input") or config.get("inputs") or []:
        if isinstance(item, dict) and str(item.get("name") or "") == input_name:
            return True
    return False


def _is_generate_backend(value: str) -> bool:
    return _normalize_backend(value) in GENERATE_ENDPOINT_BACKENDS


def _normalize_backend(value: str) -> str:
    return value.lower().replace("-", "_").replace(" ", "_")


def _is_metrics_snapshot_available(snapshot: dict[str, Any] | None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    error = snapshot.get("error")
    return not (isinstance(error, str) and error.strip())


def _with_metric_context(
    metrics: dict[str, Any],
    source: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> dict[str, Any]:
    if metrics.get("available"):
        metrics["source"] = source
        return metrics

    before_count = _series_count(before)
    after_count = _series_count(after)
    source_label = "/metrics" if source == "metrics" else "/v2/models/stats"
    if after_count > 0:
        metrics["error"] = (
            f"{source_label} exposed {after_count} inference metric row(s), but the counters did not "
            "change during this backend inference request. Check that the configured Triton inference "
            "URL and metrics endpoint point to the same Triton server."
        )
    else:
        existing_error = metrics.get("error")
        metrics["error"] = (
            f"{source_label} did not expose inference metric rows for this backend request"
            + (f": {existing_error}" if existing_error else ".")
        )
    metrics["source"] = source
    metrics["beforeSeriesCount"] = before_count
    metrics["afterSeriesCount"] = after_count
    return metrics


def _series_count(snapshot: dict[str, Any] | None) -> int:
    series = snapshot.get("series") if isinstance(snapshot, dict) else None
    return len(series) if isinstance(series, dict) else 0
