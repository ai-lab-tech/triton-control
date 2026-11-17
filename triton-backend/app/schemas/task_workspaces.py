"""Task workspace contracts shared by handoff and experiment snapshot adapters."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.development import CodeServerDTO, CreateCodeServerRequest
from app.schemas.handoffs import ResolvedInput
from app.schemas.lineage import Snapshot
from app.schemas.preparation import Request


class WorkspaceInput(Request):
    role: Literal["source", "dataset", "baseline"]
    artifact: ResolvedInput


class WorkspaceInputs(Request):
    schema_version: Literal[1] = 1
    snapshot: Snapshot
    files: list[WorkspaceInput] = Field(min_length=1, max_length=1000)
    expected_behavior: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def distinct_files(self) -> "WorkspaceInputs":
        paths = [(item.role, item.artifact.file.path) for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Workspace input paths must be unique")
        if {item.role for item in self.files} != {"source", "dataset", "baseline"}:
            raise ValueError("Source, dataset and baseline inputs are required")
        return self


class OpenTaskWorkspace(Request):
    workspace: CreateCodeServerRequest = Field(default_factory=CreateCodeServerRequest)
    admin_recovery_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def local_registry_only(self) -> "OpenTaskWorkspace":
        if self.workspace.dockerconfigjson:
            raise ValueError("Use the installation registry configuration, not inline credentials")
        return self


class TaskWorkspaceDTO(Request):
    task_id: UUID
    workspace: CodeServerDTO
