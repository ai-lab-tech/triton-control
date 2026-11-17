"""Qualification gate for future development build execution."""

from sqlmodel import Session

from app.exceptions import ForbiddenError
from app.repositories import build_qualifications
from app.schemas.build_policy import BuildPolicy

REQUIRED_CHECKS = frozenset(
    {
        "cleanup",
        "provenance",
        "private_base_pull",
        "scoped_push",
        "denied_push",
        "context_exclusion",
        "digest_verified",
        "cancellation",
        "timeout",
        "no_host_socket",
        "no_service_account_token",
        "pod_policy",
        "network_policy",
        "staging_roundtrip",
        "versioned_https_context",
        "runtime_pull",
    }
)


def all_checks_passed(checks: dict[str, bool]) -> bool:
    return all(checks.get(name) is True for name in REQUIRED_CHECKS)


def require_qualified(session: Session, policy: BuildPolicy, fixture_revision: str, *, mode: str) -> None:
    if mode != "development" or not policy.enabled:
        raise ForbiddenError("Runtime builds are disabled")
    result = build_qualifications.latest(session, policy.fingerprint(), fixture_revision)
    if result is None or not result.passed or not all_checks_passed(result.checks):
        raise ForbiddenError("This build policy and fixture revision have not passed qualification")
    if result.executor_image != policy.executor_image or result.platform != policy.platform:
        raise ForbiddenError("Builder qualification does not match the requested platform or image")
