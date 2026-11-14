"""Team transport contracts; validation has no persistence or authorization I/O."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TeamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    is_active: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class TeamDTO(BaseModel):
    id: UUID
    name: str
    is_active: bool
    created_at: datetime


class TeamMemberDTO(BaseModel):
    user_id: int
    name: str
    email: str
    role: str
    is_active: bool
    user_is_active: bool
    eligible: bool
    updated_at: datetime


class TeamEventDTO(BaseModel):
    id: int
    actor_user_id: int
    subject_user_id: int | None
    action: str
    changes: dict[str, Any]
    created_at: datetime
