"""Fail-closed administrator provisioning gate, not a source-supplied approval."""

from app.schemas.materialization import MaterializerPolicy
from app.services.materialization.normalization import MaterializationError

REQUIRED_CHECKS = frozenset(
    {
        "exact_versions",
        "source_hash",
        "prepared_hash",
        "missing_file",
        "extra_file",
        "unsafe_path",
        "interrupted_download",
        "restart",
        "readonly_mount",
        "normalization",
        "changed_helper",
        "no_runtime_install",
    }
)


def require_qualified(policy: MaterializerPolicy) -> None:
    result = policy.qualification
    if result.image != policy.image or result.platform != policy.platform:
        raise MaterializationError("Materializer qualification does not match image/platform")
    if not all(result.checks.get(name) is True for name in REQUIRED_CHECKS):
        raise MaterializationError("Materializer qualification is incomplete or failed")
