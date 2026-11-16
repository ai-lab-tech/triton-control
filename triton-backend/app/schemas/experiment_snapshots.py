"""Versioned experiment-capture requests and immutable receipts; no I/O."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.candidate_import import ArtifactLocation
from app.schemas.lineage import Digest, FileEntry, InventoryV1, Origin, Snapshot
from app.schemas.operations import OperationResponse
from app.schemas.preparation import TaskDTO
from app.schemas.serving_metadata import Image, Path, StartupModel


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class RepositoryInput(Contract):
    profile_id: Annotated[int, Field(strict=True, gt=0)]
    prefix: Path


class WorkspaceInput(Contract):
    workspace_id: Annotated[int, Field(strict=True, gt=0)]
    files: tuple[Path, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.files)) != len(self.files):
            raise ValueError("Workspace file paths must be unique")
        return self


class PreparationRequest(Contract):
    schema_version: Literal[1] = 1
    title: Annotated[str, Field(min_length=1, max_length=200)]
    models: tuple[StartupModel, ...] = Field(min_length=1, max_length=100)
    deployment_uid: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")] | None = None
    expected_generation: Annotated[int, Field(strict=True, ge=1)] | None = None
    repository: RepositoryInput | None = None
    workspace: WorkspaceInput | None = None
    previous_snapshot_id: UUID | None = None
    supplied_runtime_image: Image | None = None
    runtime_changes: Literal["unknown", "none_declared", "requires_rebuild"] = "unknown"
    evaluation_files: tuple[FileEntry, ...] = Field(default=(), max_length=100)
    expected_behavior: Annotated[str, Field(min_length=1, max_length=8000)] | None = None
    acceptance_criteria: Annotated[str, Field(min_length=1, max_length=8000)] | None = None

    @model_validator(mode="after")
    def unique_models(self) -> Self:
        if len({model.name for model in self.models}) != len(self.models):
            raise ValueError("Intended models must be unique")
        return self


class RuntimeImage(Contract):
    declared: str | None
    observed: str | None
    node: str | None
    pod_uid: str
    restart_count: int = 0
    platform: str | None = None


class RuntimeObservation(Contract):
    managed: bool
    deployment_uid: str | None = None
    generation: int | None = None
    observed_generation: int | None = None
    resource_version: str | None = None
    images: tuple[RuntimeImage, ...] = ()
    resources: dict[str, dict[str, str]] = Field(default_factory=dict)


class SnapshotManifest(Contract):
    schema_version: Literal[1] = 1
    origin: Origin
    instance_id: int
    instance_name: str
    submitter_user_id: int
    captured_at: datetime
    previous_snapshot_id: UUID | None
    request: PreparationRequest
    runtime: RuntimeObservation
    source_receipt: dict[str, Any]
    configuration: dict[str, Any]
    configuration_digest: Digest
    source_snapshot: Snapshot
    inventory: InventoryV1
    workspace_inventory: InventoryV1
    gaps: tuple[str, ...]
    workspace_relation: Literal["not_selected", "selected_unverified_changes"]


class SnapshotReceipt(Contract):
    id: UUID
    operation_id: str
    manifest_digest: Digest
    manifest: SnapshotManifest
    artifacts: ArtifactLocation
    task: TaskDTO


class PreparationAccepted(Contract):
    operation: OperationResponse
    snapshot_url: str
