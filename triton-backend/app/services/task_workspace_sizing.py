"""Size writable task storage independently of submission admission limits."""

from decimal import Decimal

from kubernetes.utils.quantity import parse_quantity  # type: ignore[import-untyped]

from app.exceptions import BadRequestError
from app.schemas.preparation_settings import PreparationLimits

GIB = 1024**3


def recommended_gib(input_bytes: int) -> int:
    extra = max(5 * GIB, (input_bytes + 1) // 2)
    return (input_bytes + extra + GIB - 1) // GIB


def storage_size(input_bytes: int, limits: PreparationLimits, requested: str | None = None) -> str:
    minimum = recommended_gib(input_bytes)
    if minimum > limits.max_workspace_storage_gib:
        raise BadRequestError(
            f"Task requires {minimum} GiB of workspace storage; administrator maximum is "
            f"{limits.max_workspace_storage_gib} GiB",
        )
    if requested is None:
        return f"{minimum}Gi"
    try:
        size = parse_quantity(requested)
        if not size.is_finite() or not Decimal(minimum * GIB) <= size <= limits.max_workspace_storage_gib * GIB:
            raise ValueError
    except (ValueError, ArithmeticError):
        raise BadRequestError(
            f"Workspace storage must be between {minimum} and {limits.max_workspace_storage_gib} GiB",
        ) from None
    return requested
