"""Versioned experiment-capture requests and immutable receipts; no I/O."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

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


SourceLabel = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\x00-\x1f\x7f]+$")]
RepositoryName = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+$")]
GitCommit = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
ProjectPath = Path | Literal["."]


class SourceInput(Contract):
    model_card: Path | None = None
    license_file: Path | None = None
    source_documents: tuple[Path, ...] = Field(default=(), max_length=50)
    workspace_model_root: ProjectPath | None = None


class HFSource(SourceInput):
    kind: Literal["hf"]
    repository: RepositoryName | None = None
    revision: SourceLabel | None = None
    resolved_commit: GitCommit | None = None
    serving_manifest: Path | None = None
    acquisition_record: Path | None = Field(default=None, exclude_if=lambda value: value is None)
    checkpoint_files: tuple[Path, ...] = Field(default=(), max_length=200)


class ExampleSource(SourceInput):
    kind: Literal["example"]
    repository: RepositoryName | None = None
    commit: GitCommit | None = None
    project_path: ProjectPath | None = None


class ManualSource(SourceInput):
    kind: Literal["manual"]
    author: SourceLabel | None = None
    ai_assisted: bool | None = None


CaptureSource = Annotated[HFSource | ExampleSource | ManualSource, Field(discriminator="kind")]


class SourceProvenance(Contract):
    submitter_user_id: int
    documents: tuple[FileEntry, ...]
    serving_manifest: FileEntry | None = None
    generator: SourceLabel | None = None
    generator_version: SourceLabel | None = None
    generator_options: dict[str, str | int] = Field(default_factory=dict)
    checkpoint_inventory: Digest | None = None
    revision_fingerprint: Digest | None = None


class PreparationRequest(Contract):
    schema_version: Literal[1] = 1
    title: Annotated[str, Field(min_length=1, max_length=200)]
    models: tuple[StartupModel, ...] = Field(min_length=1, max_length=100)
    deployment_uid: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9-]+$")] | None = None
    expected_generation: Annotated[int, Field(strict=True, ge=1)] | None = None
    repository: RepositoryInput | None = None
    workspace: WorkspaceInput | None = None
    source: CaptureSource | None = Field(default=None, exclude_if=lambda value: value is None)
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
        if self.source:
            selected = set(self.workspace.files) if self.workspace else set()
            documents = (self.source.model_card, self.source.license_file, *self.source.source_documents)
            if isinstance(self.source, HFSource):
                documents = (*documents, self.source.acquisition_record)
            if isinstance(self.source, HFSource) and self.source.serving_manifest:
                if self.source.serving_manifest.rsplit("/", 1)[-1] != "serving.json":
                    raise ValueError("The serving manifest must be named serving.json")
                documents = (*documents, self.source.serving_manifest)
            if any(path is not None and path not in selected for path in documents):
                raise ValueError("Select source documents and serving.json in workspace.files")
            if isinstance(self.source, ManualSource) and not selected:
                raise ValueError("Manual capture requires inventoried workspace files")
            root = self.source.workspace_model_root
            if root:
                prefix = "" if root == "." else f"{root}/"
                prefixes = tuple(f"{prefix}{model.name}/" for model in self.models)
                if not any(path.startswith(prefixes) for path in selected):
                    raise ValueError("Select files for an intended model under workspace_model_root")
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
    source_provenance: SourceProvenance | None = Field(default=None, exclude_if=lambda value: value is None)
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
