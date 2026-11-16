"""Neutral prepared repository import contracts; no I/O."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.lineage import (
    Contract,
    Digest,
    EngineeringAttempt,
    InventoryV1,
    Origin,
    PluginReference,
    ServingCandidateV1,
    Snapshot,
)

InputName = Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")]


class PreparedImportRequest(Contract):
    schema_version: Literal[1] = 1
    candidate_origin: Origin
    capture_origin: Origin
    # A manual repository can be the source itself. Other paths supply their exact
    # captured source and actual prior attempts, rather than inventing run IDs.
    manual_source_origin: Origin | None = None
    source_snapshot: Snapshot | None = None
    attempts: tuple[EngineeringAttempt, ...] = Field(default=(), max_length=64)
    prior_inventories: tuple[InventoryV1, ...] = Field(default=(), max_length=65)
    previous_serving_candidate: Origin | None = None
    plugin_references: tuple[PluginReference, ...] = Field(default=(), max_length=32)
    triton_version: str = Field(min_length=1, max_length=40)
    expected_backends: dict[str, str] = Field(default_factory=dict, max_length=100)
    missing_evaluation_inputs: tuple[InputName, ...] = Field(
        default=("evaluation_dataset", "expected_results", "acceptance_thresholds"),
        max_length=50,
    )
    task_id: UUID | None = None
    expected_task_revision: int | None = Field(default=None, strict=True, ge=1)

    @model_validator(mode="after")
    def source_and_task(self) -> Self:
        if (self.manual_source_origin is None) == (self.source_snapshot is None):
            raise ValueError("Supply exactly one manual source origin or captured source snapshot")
        if self.source_snapshot is None and (self.attempts or self.prior_inventories):
            raise ValueError("Prior attempts require their exact source snapshot and inventories")
        if (self.task_id is None) != (self.expected_task_revision is None):
            raise ValueError("Task ID and expected task revision must be supplied together")
        if self.capture_origin.installation_id != self.candidate_origin.installation_id:
            raise ValueError("Capture and candidate belong to the importing installation")
        return self


class ModelInventory(Contract):
    name: str
    backend: str
    versions: tuple[str, ...]


class ArtifactLocation(Contract):
    kind: Literal["directory", "s3"]
    # Only opaque snapshot IDs or bucket/key/version references, never credentials.
    reference: dict[str, Any]


class ImportedCandidate(Contract):
    schema_version: Literal[1] = 1
    candidate: ServingCandidateV1
    candidate_digest: Digest
    inventory: InventoryV1
    metadata_inventory: InventoryV1
    models: tuple[ModelInventory, ...]
    missing_evaluation_inputs: tuple[str, ...]
    completeness: Literal["evaluation_inputs_missing", "evaluation_inputs_declared"]
    artifacts: ArtifactLocation
    source_receipt: dict[str, Any]
    owner_user_id: int
    task_id: UUID | None
    created_at: datetime
