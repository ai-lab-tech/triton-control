"""Admission and disk accounting for an administrator-supplied capture budget."""

import math
from typing import Any

from app.schemas.materialization import MaterializationPlan
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.normalization import MaterializationError


def check_plan(plan: MaterializationPlan, limits: CaptureLimits) -> None:
    for inventory in (plan.source_inventory, plan.prepared_inventory):
        if (
            len(inventory.files) > limits.max_files
            or sum(entry.size for entry in inventory.files) > limits.max_bytes
            or any(entry.size > limits.max_file_bytes for entry in inventory.files)
        ):
            raise MaterializationError("Materialization inventory exceeds the configured budget")


def storage_bytes(plan: MaterializationPlan) -> int:
    # Source and prepared bytes coexist until the pod is deleted. Reserve metadata headroom too.
    return (
        sum(entry.size for inventory in (plan.source_inventory, plan.prepared_inventory) for entry in inventory.files)
        + 1024**3
    )


def configured_limits(values: dict[str, Any]) -> CaptureLimits:
    required = {"max_bytes", "max_file_bytes", "deadline_seconds"}
    if not isinstance(values, dict) or not required <= values.keys():
        raise MaterializationError("Explicit byte, file and timeout budgets are required")
    for name, value in values.items():
        valid_type = type(value) in {int, float} if name == "deadline_seconds" else type(value) is int
        if not valid_type or not math.isfinite(value) or value <= 0:
            raise MaterializationError("Materialization budgets must be positive finite numbers")
    return CaptureLimits(**values)
