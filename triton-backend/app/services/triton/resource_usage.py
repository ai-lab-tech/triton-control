"""Merge independently sampled Kubernetes and Triton telemetry."""

from typing import Any

from app.db.entities import TritonInstanceEntity

GPU_FIELDS = frozenset({"gpu_percent", "gpu_memory_used_bytes", "gpu_memory_total_bytes", "gpu_error"})


def apply_triton_sample(instance: TritonInstanceEntity, sample: dict[str, Any]) -> None:
    usage = dict(instance.resource_usage or {})
    for field in GPU_FIELDS:
        usage.pop(field, None)
    usage.update(sample.get("resource_usage") or {})
    instance.resource_usage = usage
    instance.metrics_cpu = sample["cpu"]
    instance.metrics_ram = sample["ram"]
    instance.metrics_gpu = sample["gpu"]
    instance.metrics_last_checked_at = sample["checked_at"]
    instance.metrics_error = sample["error"]


def apply_workload_sample(instance: TritonInstanceEntity, workload: dict[str, Any]) -> None:
    gpu = {key: value for key, value in (instance.resource_usage or {}).items() if key in GPU_FIELDS}
    instance.resource_usage = {**workload, **gpu}
