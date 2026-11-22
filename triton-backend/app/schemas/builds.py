"""Pure contracts for task-owned runtime image builds."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.operations import OperationResponse


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: UUID
    context_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    repository: str = Field(min_length=1, max_length=256)
    tag: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


class BuildAttemptResponse(BaseModel):
    number: int
    context_id: str
    executor_image: str
    destination: str
    namespace: str
    job_name: str
    job_uid: str | None
    state: str
    final_digest: str | None
    created_at: datetime
    finished_at: datetime | None
    logs_expires_at: datetime | None
    cleaned: bool


class BuildResponse(BaseModel):
    id: str
    task_id: UUID
    actor_id: int
    operation: OperationResponse
    attempts: list[BuildAttemptResponse]


class BuildLogsResponse(BaseModel):
    attempt: int
    output: str
    truncated: bool
    expired: bool
    retained: bool


class BuildOptions(BaseModel):
    task_id: UUID
    repositories: tuple[str, ...]
    max_context_bytes: int
    local_archive: bool = False
    staging_profiles: tuple[str, ...] = ()
