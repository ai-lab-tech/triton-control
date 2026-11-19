"""One effective policy shared by capture, downloads and workspace admission."""

import os
from typing import Any

from pydantic import ValidationError
from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import ServiceUnavailableError
from app.repositories import preparation_config
from app.schemas.preparation_settings import PreparationLimits, PreparationSettings

ENV_PREFIX = "PREPARATION_"


def defaults() -> PreparationLimits:
    try:
        return PreparationLimits(**{
            name: int(os.environ[ENV_PREFIX + name.upper()])
            for name in PreparationLimits.model_fields if ENV_PREFIX + name.upper() in os.environ
        })
    except (ValueError, ValidationError):
        raise ServiceUnavailableError("Invalid model preparation deployment limits") from None


def saved_limits(values: dict[str, Any]) -> PreparationLimits:
    """Read overrides saved before the policy was simplified; writes use only the new schema."""
    if "max_input_gib" not in values and "max_workspace_gib" in values:
        values = {
            "max_input_gib": values["max_workspace_gib"],
            "max_workspace_storage_gib": values.get("max_workspace_storage_gib", 50),
            "transfer_timeout_seconds": min(values.get("capture_timeout_seconds", 3600),
                                            values.get("download_timeout_seconds", 3600)),
        }
    return PreparationLimits.model_validate(values)


def effective(session: Session) -> PreparationLimits:
    row = preparation_config.get(session)
    return saved_limits(row.limits) if row else defaults()


def read(session: Session, claims: dict[str, Any]) -> PreparationSettings:
    require_admin(claims)
    row = preparation_config.get(session)
    base = defaults()
    return PreparationSettings(limits=saved_limits(row.limits) if row else base,
                               defaults=base, source="admin" if row else "deployment")


def update(session: Session, claims: dict[str, Any], limits: PreparationLimits | None) -> PreparationSettings:
    require_admin(claims)
    defaults()  # Do not hide invalid deployment configuration behind an override.
    preparation_config.save(session, limits.model_dump() if limits else None)
    return read(session, claims)
