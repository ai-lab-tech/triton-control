"""Read models for the engineering queue; statuses keep their original meaning."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.preparation import TaskDTO


class TaskSource(BaseModel):
    kind: str = "unavailable"
    revision: str | None = None
    models: list[str] = Field(default_factory=list)
    handoff_id: UUID | None = None
    snapshot_id: UUID | None = None
    instance_id: int | None = None

class TaskActions(BaseModel):
    claim: bool = False
    release: bool = False
    request_information: bool = False
    open_workspace: bool = False

class QueueEntry(BaseModel):
    task: TaskDTO
    source: TaskSource
    team_name: str
    submitter_name: str
    assignee_name: str | None
    actions: TaskActions

class CandidateSummary(BaseModel):
    installation_id: str
    record_id: str
    candidate_digest: str
    created_at: datetime

class TaskOverview(QueueEntry):
    candidates: list[CandidateSummary] = Field(default_factory=list)
