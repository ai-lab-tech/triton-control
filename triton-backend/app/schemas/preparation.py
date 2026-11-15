"""Versioned preparation task contracts; no I/O or resource-access grants."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.lineage import EngineeringAttempt

InputName = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")]
TaskState = Literal["queued", "claimed", "awaiting_information", "cancelled"]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TaskCreate(Request):
    """Internal creation contract for future authorized handoff/snapshot services."""

    title: str = Field(min_length=1, max_length=200)
    source_snapshot_id: UUID | None = None
    missing_inputs: list[InputName] = Field(default_factory=list, max_length=50)


class TaskRevision(Request):
    expected_revision: int = Field(strict=True, ge=1)


class InformationRequest(TaskRevision):
    missing_inputs: list[InputName] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=2000)


class ResolveInputsRequest(TaskRevision):
    resolved_inputs: list[InputName] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=2000)


class ReasonRequest(TaskRevision):
    reason: str = Field(min_length=1, max_length=2000)


class ReassignRequest(ReasonRequest):
    assignee_user_id: int | None = Field(default=None, strict=True, ge=1)


class TaskDTO(BaseModel):
    schema_version: Literal[1] = 1
    id: UUID
    title: str
    assigned_team_id: UUID
    submitter_user_id: int
    assignee_user_id: int | None
    source_snapshot_id: UUID | None
    state: TaskState
    revision: int
    missing_inputs: list[str]
    blocked_reasons: list[str]
    created_at: datetime
    updated_at: datetime


class TaskEventDTO(BaseModel):
    id: int
    revision: int
    actor_user_id: int
    action: str
    changes: dict[str, Any]
    created_at: datetime


class AttemptDTO(BaseModel):
    id: UUID
    actor_user_id: int
    attempt: EngineeringAttempt
    created_at: datetime
