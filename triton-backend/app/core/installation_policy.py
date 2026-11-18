"""Startup-owned capability ceiling; roles/resource grants can only restrict it further."""

import os
from functools import lru_cache
from typing import Literal

from app.exceptions import ForbiddenError
from app.schemas.capabilities import Capabilities, Capability

Mode = Literal["development", "production"]

# Production changes stay unavailable until independent approval and execution ship.
PRODUCTION = frozenset({"serving_read", "artifact_read", "identity_admin", "credential_admin"})
CAPABILITIES: tuple[Capability, ...] = (
    "serving_read", "artifact_read", "interactive_inference", "development_tools", "development_jobs",
    "model_write", "artifact_write", "direct_deployment", "identity_admin", "credential_admin", "trust_admin",
    "production_changes",
)
ADMIN = frozenset({"identity_admin", "credential_admin", "trust_admin"})
READ = frozenset({"serving_read", "artifact_read", "interactive_inference", "development_tools"})


@lru_cache(maxsize=1)
def mode() -> Mode:
    value = os.getenv("TRITON_CONTROL_MODE", "development")
    if value not in {"development", "production"}:
        raise RuntimeError("TRITON_CONTROL_MODE must be exactly development or production")
    return "production" if value == "production" else "development"


def allowed(capability: Capability) -> bool:
    return capability != "production_changes" and (mode() == "development" or capability in PRODUCTION)


def require(capability: Capability) -> None:
    if not allowed(capability):
        raise ForbiddenError(f"{capability} is unavailable in {mode()} mode; installation policy cannot be overridden")


def capabilities(role: str) -> Capabilities:
    role = role.strip().lower()
    values = {key: allowed(key) and (
        role == "admin" or role == "member" and key not in ADMIN or role == "viewer" and key in READ
    ) for key in CAPABILITIES}
    return Capabilities(mode=mode(), capabilities=values,
                        explanation="Production — controlled changes" if mode() == "production" else "Development")
