"""Task workspace contracts shared by handoff and experiment snapshot adapters."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.development import CodeServerDTO, CreateCodeServerRequest
from app.schemas.handoffs import ResolvedInput
from app.schemas.lineage import FileEntry, Snapshot
from app.schemas.preparation import Request


class SnapshotFile(Request):
    kind: Literal["experiment_snapshot"] = "experiment_snapshot"
    snapshot_id: UUID
    file: FileEntry


class WorkspaceInput(Request):
    role: Literal["source", "dataset", "baseline"]
    artifact: ResolvedInput | SnapshotFile


class WorkspaceInputs(Request):
    schema_version: Literal[1] = 1
    snapshot: Snapshot
    files: list[WorkspaceInput] = Field(min_length=1, max_length=10200)
    expected_behavior: str | None = Field(default=None, min_length=1, max_length=8000)
    acceptance_criteria: str | None = Field(default=None, min_length=1, max_length=8000)

    @model_validator(mode="after")
    def distinct_files(self) -> "WorkspaceInputs":
        paths = [(item.role, item.artifact.file.path) for item in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("Workspace input paths must be unique")
        snapshots = [item.artifact for item in self.files if isinstance(item.artifact, SnapshotFile)]
        if snapshots:
            if (len(snapshots) != len(self.files) or len({item.snapshot_id for item in snapshots}) != 1
                    or any(item.role != "source" for item in self.files)):
                raise ValueError("Snapshot inputs must belong to one snapshot and retain their source inventory")
        elif len(self.files) > 1000:
            raise ValueError("Handoff workspace inputs exceed the 1000 file limit")
        elif not any(item.role == "source" for item in self.files):
            raise ValueError("A workspace model source is required")
        return self


class OpenTaskWorkspace(Request):
    workspace: CreateCodeServerRequest = Field(default_factory=CreateCodeServerRequest)
    admin_recovery_reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def local_registry_only(self) -> "OpenTaskWorkspace":
        if self.workspace.dockerconfigjson:
            raise ValueError("Use the installation registry configuration, not inline credentials")
        if self.workspace.gpu_count is not None and self.workspace.gpu_count < 0:
            raise ValueError("GPU count must be zero or greater")
        return self


class TaskWorkspaceDTO(Request):
    task_id: UUID
    workspace: CodeServerDTO


class WorkspaceSizing(Request):
    input_bytes: int
    recommended_gib: int
    maximum_gib: int
    existing_storage_size: str | None = None
    default_image: str
    existing_image: str | None = None
    existing_gpu_count: int | None = None
    existing_cpu: str | None = None
    existing_memory: str | None = None
