"""Async HTTP client for the Triton Inference Server REST API.

Provides ``TritonService``, which wraps ``httpx.AsyncClient`` to communicate
with a single Triton instance.  Client instances are pooled by URL+timeout
to avoid creating a new connection pool on every request.

Supported operations:
  ``is_ready`` / ``is_live``         — health-check endpoints.
  ``collect_runtime_snapshot``       — combined liveness, readiness, and
                                        server-metadata fetch in one pass.
  ``get_server_metadata``            — server version and extension info.
  ``get_model_names``                — extract model names from metadata.
  ``get_repository_index``           — model repository listing.
  ``get_model_config``               — per-model protobuf config.
  ``load_model`` / ``unload_model``  — model lifecycle control.
  ``infer``                          — raw inference proxy.
"""

import asyncio
import os
import re
import ssl
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import httpx

from app.core.tls import create_default_context_with_extra_ca


class TritonService:
    """Service to interact with Triton Inference Server."""
    _clients: dict[Tuple[str, float, str, bool], httpx.AsyncClient] = {}

    def __init__(
        self,
        triton_url: str,
        verify_ssl: bool = False,
        ca_certificate: str = "",
        timeout: float = 30.0,
    ):
        """
        Initialize Triton service.

        Args:
            triton_url: Base URL of Triton server (e.g., http://localhost:8000)
        """
        self.triton_url = triton_url.rstrip('/')
        self.timeout = timeout
        self.verify_ssl = bool(verify_ssl)
        self.ca_certificate = (ca_certificate or "").strip()
        self.verify = self._build_verify()
        self.verify_cache_key = self._build_verify_cache_key()
        self.trust_env = self._trust_env()

    def _build_verify(self) -> bool | ssl.SSLContext:
        if not self.verify_ssl:
            return False
        if not self.ca_certificate:
            return True
        return create_default_context_with_extra_ca(self.ca_certificate, "Triton")

    def _build_verify_cache_key(self) -> str:
        if not self.verify_ssl:
            return "verify:false"
        if not self.ca_certificate:
            return "verify:true"
        digest = sha256(self.ca_certificate.encode("utf-8")).hexdigest()
        return f"verify:ca:{digest}"

    @staticmethod
    def _trust_env() -> bool:
        return os.getenv("TRITON_HTTP_TRUST_ENV", "").strip().lower() in {"1", "true", "yes", "on"}

    def _get_client(self) -> httpx.AsyncClient:
        key = (self.triton_url, self.timeout, self.verify_cache_key, self.trust_env)
        client = self._clients.get(key)
        if client is not None:
            return client

        client = httpx.AsyncClient(timeout=self.timeout, verify=self.verify, trust_env=self.trust_env)
        self._clients[key] = client
        return client

    @classmethod
    async def close_all_clients(cls) -> None:
        clients = list(cls._clients.values())
        cls._clients.clear()
        for client in clients:
            await client.aclose()

    async def is_ready(self) -> bool:
        """
        Check if Triton server is ready.

        Returns:
            True if server is ready, False otherwise
        """
        try:
            url = f"{self.triton_url}/v2/health/ready"
            response = await self._get_client().get(url)
            return response.status_code == 200
        except Exception:
            return False

    async def collect_runtime_snapshot(self, include_metadata: bool = True) -> Dict[str, Any]:
        """Collect health endpoints and optionally /v2 metadata in one pass."""
        client = self._get_client()

        async def _fetch_metadata() -> tuple[Dict[str, Any] | None, str | None]:
            metadata_url = f"{self.triton_url}/v2"
            try:
                response = await client.get(metadata_url)
                if response.status_code == 200:
                    payload = response.json()
                    if isinstance(payload, dict):
                        return payload, None
                    return None, "v2 returned non-object JSON"
                return None, f"v2 returned HTTP {response.status_code}"
            except Exception as exc:
                return None, f"v2 request failed: {exc}"

        async def _fetch_health(name: str, path: str) -> tuple[bool, str | None]:
            url = f"{self.triton_url}{path}"
            try:
                response = await client.get(url)
                healthy = response.status_code == 200
                return healthy, None if healthy else f"{name} returned HTTP {response.status_code}"
            except Exception as exc:
                return False, f"{name} request failed: {exc}"

        metadata: Dict[str, Any] | None = None
        errors: List[str] = []
        if include_metadata:
            metadata, metadata_error = await _fetch_metadata()
            if metadata_error:
                errors.append(metadata_error)

        live_result, ready_result = await asyncio.gather(
            _fetch_health("live", "/v2/health/live"),
            _fetch_health("ready", "/v2/health/ready"),
        )
        live, live_error = live_result
        ready, ready_error = ready_result
        if live_error:
            errors.append(live_error)
        if ready_error:
            errors.append(ready_error)

        return {
            "metadata": metadata,
            "live": live,
            "ready": ready,
            "checked_at": datetime.now(timezone.utc),
            "error": "; ".join(errors) if errors else None,
        }

    async def collect_metrics_snapshot(self, metrics_url: str | None) -> Dict[str, Any]:
        """Fetch and parse Triton Prometheus metrics for host utilization."""
        if not metrics_url:
            return {
                "cpu": 0.0,
                "ram": 0.0,
                "gpu": 0.0,
                "checked_at": None,
                "error": None,
            }

        try:
            response = await self._get_client().get(metrics_url)
            response.raise_for_status()
            text = response.content.decode("utf-8", errors="replace")
            metrics = self._parse_prometheus_metrics(text)
            return {
                "cpu": metrics["cpu"],
                "ram": metrics["ram"],
                "gpu": metrics["gpu"],
                "checked_at": datetime.now(timezone.utc),
                "error": metrics["error"],
            }
        except Exception as exc:
            return {
                "cpu": 0.0,
                "ram": 0.0,
                "gpu": 0.0,
                "checked_at": datetime.now(timezone.utc),
                "error": f"metrics request failed: {exc}",
            }

    async def collect_inference_metrics_snapshot(self, metrics_url: str | None) -> Dict[str, Any]:
        """Fetch cumulative Triton inference latency counters."""
        if not metrics_url:
            return {"series": {}, "error": "Metrics endpoint is not configured."}

        try:
            response = await self._get_client().get(metrics_url)
            response.raise_for_status()
            text = response.content.decode("utf-8", errors="replace")
            return {
                "series": self._parse_inference_metric_series(text),
                "error": None,
            }
        except Exception as exc:
            return {"series": {}, "error": f"metrics request failed: {exc}"}

    async def collect_inference_stats_snapshot(self) -> Dict[str, Any]:
        """Fetch cumulative Triton model statistics for inference latency."""
        try:
            response = await self._get_client().get(f"{self.triton_url}/v2/models/stats")
            response.raise_for_status()
            payload = response.json()
            return {
                "series": self._parse_inference_stats_series(payload),
                "error": None,
            }
        except Exception as exc:
            return {"series": {}, "error": f"stats request failed: {exc}"}

    @staticmethod
    def _parse_prometheus_metrics(text: str) -> Dict[str, Any]:
        values: dict[str, list[float]] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            name = parts[0].split("{", 1)[0]
            try:
                value = float(parts[1])
            except ValueError:
                continue

            values.setdefault(name, []).append(value)

        cpu_values = TritonService._metric_values(values, ["nv_cpu_utilization", "cpu_utilization"])
        gpu_values = TritonService._metric_values(values, ["nv_gpu_utilization", "gpu_utilization"])

        ram_used = sum(
            TritonService._metric_values(
                values,
                ["nv_cpu_memory_used_bytes", "nv_cpu_memory_used"],
            ),
        )
        ram_total = sum(
            TritonService._metric_values(
                values,
                ["nv_cpu_memory_total_bytes", "nv_cpu_memory_total"],
            ),
        )
        ram_free = sum(
            TritonService._metric_values(
                values,
                ["nv_cpu_memory_free_bytes", "nv_cpu_memory_available_bytes"],
            ),
        )

        if ram_total > 0 and ram_used <= 0 and ram_free > 0:
            ram_used = max(0.0, ram_total - ram_free)

        cpu = TritonService._average(cpu_values)
        gpu = TritonService._average(gpu_values)
        ram = (ram_used / ram_total * 100) if ram_total > 0 else 0.0
        missing: list[str] = []
        if not cpu_values:
            missing.append("nv_cpu_utilization")
        if ram_total <= 0 or ram_used <= 0:
            missing.append("nv_cpu_memory_used_bytes/nv_cpu_memory_total_bytes")

        return {
            "cpu": TritonService._clamp_percent(cpu),
            "ram": TritonService._clamp_percent(ram),
            "gpu": TritonService._clamp_percent(gpu),
            "error": (
                "Metrics endpoint does not expose Triton CPU/RAM metrics: "
                + ", ".join(missing)
                + ". Enable Triton CPU metrics or configure the correct metrics endpoint."
                if missing
                else None
            ),
        }

    @staticmethod
    def _parse_inference_metric_series(text: str) -> Dict[str, Dict[str, Any]]:
        metric_names = {
            "nv_inference_count",
            "nv_inference_exec_count",
            "nv_inference_request_success",
            "nv_inference_request_duration_us",
            "nv_inference_queue_duration_us",
            "nv_inference_compute_input_duration_us",
            "nv_inference_compute_infer_duration_us",
            "nv_inference_compute_output_duration_us",
        }
        series: dict[str, dict[str, Any]] = {}

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = line.split()
            if len(parts) < 2:
                continue

            metric_name = parts[0].split("{", 1)[0]
            if metric_name not in metric_names:
                continue

            try:
                value = float(parts[1])
            except ValueError:
                continue

            labels = TritonService._parse_prometheus_labels(parts[0])
            model = labels.get("model") or labels.get("model_name") or ""
            version = labels.get("version") or labels.get("model_version") or ""
            key = f"{model}|{version}"
            row = series.setdefault(
                key,
                {
                    "model": model,
                    "version": version,
                    "request_count": 0.0,
                    "total_us": 0.0,
                    "queue_us": 0.0,
                    "input_us": 0.0,
                    "infer_us": 0.0,
                    "output_us": 0.0,
                },
            )

            if metric_name in {"nv_inference_count", "nv_inference_exec_count", "nv_inference_request_success"}:
                row["request_count"] += value
            elif metric_name == "nv_inference_request_duration_us":
                row["total_us"] += value
            elif metric_name == "nv_inference_queue_duration_us":
                row["queue_us"] += value
            elif metric_name == "nv_inference_compute_input_duration_us":
                row["input_us"] += value
            elif metric_name == "nv_inference_compute_infer_duration_us":
                row["infer_us"] += value
            elif metric_name == "nv_inference_compute_output_duration_us":
                row["output_us"] += value

        return series

    @staticmethod
    def _parse_inference_stats_series(payload: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(payload, dict):
            return {}

        model_stats = payload.get("model_stats")
        if not isinstance(model_stats, list):
            return {}

        series: dict[str, dict[str, Any]] = {}
        for item in model_stats:
            if not isinstance(item, dict):
                continue

            model = str(item.get("name") or "")
            version = str(item.get("version") or "")
            inference_stats = item.get("inference_stats")
            if not isinstance(inference_stats, dict):
                inference_stats = {}

            success = TritonService._duration_stat(inference_stats.get("success"))
            queue = TritonService._duration_stat(inference_stats.get("queue"))
            compute_input = TritonService._duration_stat(inference_stats.get("compute_input"))
            compute_infer = TritonService._duration_stat(inference_stats.get("compute_infer"))
            compute_output = TritonService._duration_stat(inference_stats.get("compute_output"))
            request_count = float(item.get("inference_count") or success["count"])
            key = f"{model}|{version}"
            series[key] = {
                "model": model,
                "version": version,
                "request_count": request_count,
                "total_us": success["ns"] / 1000.0,
                "queue_us": queue["ns"] / 1000.0,
                "input_us": compute_input["ns"] / 1000.0,
                "infer_us": compute_infer["ns"] / 1000.0,
                "output_us": compute_output["ns"] / 1000.0,
            }

        return series

    @staticmethod
    def _duration_stat(value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {"count": 0.0, "ns": 0.0}
        return {
            "count": float(value.get("count") or 0),
            "ns": float(value.get("ns") or 0),
        }

    @staticmethod
    def _parse_prometheus_labels(metric_token: str) -> Dict[str, str]:
        match = re.search(r"\{(.*)\}", metric_token)
        if not match:
            return {}

        labels: dict[str, str] = {}
        for key, value in re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"])*)"', match.group(1)):
            labels[key] = value.replace(r"\"", '"').replace(r"\\", "\\")
        return labels

    @staticmethod
    def inference_metrics_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        before_series = before.get("series") if isinstance(before, dict) else {}
        after_series = after.get("series") if isinstance(after, dict) else {}
        if not isinstance(before_series, dict) or not isinstance(after_series, dict):
            return {"available": False, "error": "Inference metrics could not be read.", "models": []}

        models: list[dict[str, Any]] = []
        for key, after_row in after_series.items():
            if not isinstance(after_row, dict):
                continue
            before_row = before_series.get(key, {})
            if not isinstance(before_row, dict):
                before_row = {}

            count_delta = TritonService._counter_delta(after_row, before_row, "request_count")
            if count_delta <= 0 and not TritonService._has_duration_delta(after_row, before_row):
                continue
            if count_delta <= 0:
                count_delta = 1.0

            total_ms = TritonService._duration_delta_ms(after_row, before_row, "total_us", count_delta)
            queue_ms = TritonService._duration_delta_ms(after_row, before_row, "queue_us", count_delta)
            input_ms = TritonService._duration_delta_ms(after_row, before_row, "input_us", count_delta)
            infer_ms = TritonService._duration_delta_ms(after_row, before_row, "infer_us", count_delta)
            output_ms = TritonService._duration_delta_ms(after_row, before_row, "output_us", count_delta)

            models.append(
                {
                    "model": after_row.get("model", ""),
                    "version": after_row.get("version", ""),
                    "requestCount": int(count_delta),
                    "totalMs": total_ms,
                    "queueMs": queue_ms,
                    "withoutQueueMs": max(0.0, round(total_ms - queue_ms, 3)),
                    "computeInputMs": input_ms,
                    "computeInferMs": infer_ms,
                    "computeOutputMs": output_ms,
                }
            )

        error = (
            (after.get("error") or before.get("error"))
            if isinstance(after, dict) and isinstance(before, dict)
            else None
        )
        if not models:
            return {
                "available": False,
                "error": error or "No Triton inference latency metrics changed for this request.",
                "models": [],
            }

        models.sort(key=lambda item: (str(item.get("model", "")).lower(), str(item.get("version", ""))))
        return {"available": True, "error": error, "models": models}

    @staticmethod
    def _duration_delta_ms(
        after_row: dict[str, Any],
        before_row: dict[str, Any],
        key: str,
        count_delta: float,
    ) -> float:
        delta_us = max(0.0, float(after_row.get(key, 0)) - float(before_row.get(key, 0)))
        return round(delta_us / count_delta / 1000.0, 3)

    @staticmethod
    def _counter_delta(after_row: dict[str, Any], before_row: dict[str, Any], key: str) -> float:
        return max(0.0, float(after_row.get(key, 0)) - float(before_row.get(key, 0)))

    @staticmethod
    def _has_duration_delta(after_row: dict[str, Any], before_row: dict[str, Any]) -> bool:
        return any(
            TritonService._counter_delta(after_row, before_row, key) > 0
            for key in ("total_us", "queue_us", "input_us", "infer_us", "output_us")
        )

    @staticmethod
    def _metric_values(values: dict[str, list[float]], names: list[str]) -> list[float]:
        result: list[float] = []
        for name in names:
            result.extend(values.get(name, []))
        return result

    @staticmethod
    def _average(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _clamp_percent(value: float) -> float:
        return max(0.0, min(100.0, round(value, 1)))

    async def get_model_names(self) -> List[str]:
        """Return the list of model names available in Triton.

        Uses the V2 HTTP API repository index endpoint and, when possible,
        prefers models that are currently READY.
        """
        url = f"{self.triton_url}/v2/repository/index"

        try:
            client = self._get_client()
            # Triton Model Repository Index is a POST endpoint.
            response = await client.post(url, json={"ready": True})

            # Backward/variant fallback if server rejects POST body.
            if response.status_code == 405:
                response = await client.post(url)

            # Extremely old/variant fallback (some proxies incorrectly allow GET).
            if response.status_code == 405:
                response = await client.get(url)

            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError:
                return []

            if not isinstance(payload, list):
                return []

            names: List[str] = []
            ready_names: List[str] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue

                name = item.get("name")
                if not isinstance(name, str):
                    continue

                names.append(name)

                state = item.get("state")
                if isinstance(state, str) and state.upper() == "READY":
                    ready_names.append(name)

            # Prefer READY models when state is available.
            selected = ready_names or names

            # Preserve deterministic output
            return sorted(set(selected))
        except Exception:
            return []

    async def get_repository_index(self) -> List[Dict[str, Any]]:
        """Return the raw Triton repository index entries."""
        url = f"{self.triton_url}/v2/repository/index"

        try:
            client = self._get_client()
            response = await client.post(url, json={"ready": False})

            if response.status_code == 405:
                response = await client.post(url)

            if response.status_code == 405:
                response = await client.get(url)

            response.raise_for_status()

            payload = response.json()

            if not isinstance(payload, list):
                return []

            return [item for item in payload if isinstance(item, dict)]
        except Exception:
            return []

    async def get_model_config(self, model_name: str, version: str) -> Any:
        """Return the Triton config JSON for a specific model version."""
        encoded_model_name = quote(model_name, safe="")
        encoded_version = quote(version, safe="")
        url = f"{self.triton_url}/v2/models/{encoded_model_name}/versions/{encoded_version}/config"

        response = await self._get_client().get(url)
        response.raise_for_status()
        return response.json()

    async def load_model(self, model_name: str) -> None:
        """Trigger explicit model load via Triton's repository API."""
        encoded_model_name = quote(model_name, safe="")
        url = f"{self.triton_url}/v2/repository/models/{encoded_model_name}/load"

        response = await self._get_client().post(url)
        response.raise_for_status()

    async def unload_model(self, model_name: str) -> None:
        """Trigger explicit model unload via Triton's repository API."""
        encoded_model_name = quote(model_name, safe="")
        url = f"{self.triton_url}/v2/repository/models/{encoded_model_name}/unload"

        response = await self._get_client().post(url)
        response.raise_for_status()

    async def infer_model(self, model_name: str, version: str, payload: Any) -> httpx.Response:
        """Send an inference request to a specific Triton model version."""
        encoded_model_name = quote(model_name, safe="")
        encoded_version = quote(version, safe="")
        url = f"{self.triton_url}/v2/models/{encoded_model_name}/versions/{encoded_version}/infer"

        response = await self._get_client().post(url, json=payload)
        response.raise_for_status()
        return response

    async def infer_model_raw(self, model_name: str, version: str, payload: bytes, content_type: str) -> httpx.Response:
        """Send an inference request to Triton without JSON re-serialization."""
        encoded_model_name = quote(model_name, safe="")
        encoded_version = quote(version, safe="")
        url = f"{self.triton_url}/v2/models/{encoded_model_name}/versions/{encoded_version}/infer"

        response = await self._get_client().post(
            url,
            content=payload,
            headers={"content-type": content_type or "application/json"},
        )
        response.raise_for_status()
        return response
