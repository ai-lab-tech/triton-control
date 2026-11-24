"""Versioned explicit handoff contracts. No storage or authorization I/O."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.control_lineage import ControlLineage
from app.schemas.lineage import Digest, FileEntry, Origin, Snapshot
from app.schemas.preparation import Request, TaskDTO
from app.schemas.serving_metadata import StartupModel


class S3Input(Request):
    kind: Literal["s3"] = "s3"
    profile_id: int = Field(strict=True, ge=1)
    path: str = Field(min_length=1, max_length=1024, pattern=r"^[^\x00-\x1f]+$")
    version_id: str | None = Field(default=None, min_length=1, max_length=1024)
    expected_digest: Digest | None = None


class S3RepositoryInput(Request):
    kind: Literal["s3_repository"]
    profile_id: int = Field(strict=True, ge=1)
    path: str = Field(min_length=1, max_length=1024, pattern=r"^[^\x00-\x1f]+$")

    model: StartupModel | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("path")
    @classmethod
    def folder_path(cls, value: str) -> str:
        # One optional trailing slash is a folder spelling, not a different identity.
        value = value.removesuffix("/")
        if not value:
            raise ValueError("Select a repository folder within the profile")
        return value


class HandoffRequest(Request):
    schema_version: Literal[1] = 1
    origin: Origin
    previous_handoff_id: UUID | None = None
    revision: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=200)
    source: S3Input | S3RepositoryInput
    lineage: ControlLineage | None = None
    dataset: S3Input | None = None
    baseline: S3Input | None = None
    expected_behavior: str | None = Field(default=None, min_length=1, max_length=8000)
    acceptance_criteria: str | None = Field(default=None, min_length=1, max_length=8000)


    @model_validator(mode="after")
    def lineage_origin(self) -> "HandoffRequest":
        if self.lineage and (self.origin != self.lineage.origin or not isinstance(self.source, S3RepositoryInput)):
            raise ValueError("MLflow handoffs require the logged-model origin and a repository source")
        return self


def missing_evaluation_inputs(
    dataset: object | None, baseline: object | None,
    expected_behavior: str | None, acceptance_criteria: str | None,
) -> list[str]:
    return [
        name for name, value in (
            ("evaluation_dataset", dataset), ("baseline", baseline),
            ("expected_behavior", expected_behavior), ("acceptance_criteria", acceptance_criteria),
        ) if value is None
    ]


class ResolvedInput(Request):
    source: S3Input
    bucket: str
    key: str
    endpoint: str
    version_id: str | None
    file: FileEntry


class HandoffSubmissionPolicy(Request):
    require_s3_versioning: bool


class ResolvedRepository(Request):
    source: S3RepositoryInput
    bucket: str
    key: str
    endpoint: str
    files: list[ResolvedInput] = Field(min_length=1, max_length=998)


class HandoffReceipt(Request):
    schema_version: Literal[1] = 1
    id: UUID
    request_digest: Digest
    request: HandoffRequest
    source_snapshot: Snapshot
    source: ResolvedInput | ResolvedRepository
    dataset: ResolvedInput | None
    baseline: ResolvedInput | None
    task: TaskDTO
    created_at: datetime


class NotificationDTO(Request):
    id: UUID
    handoff_id: UUID
    created_at: datetime
    read_at: datetime | None
    message: Literal["A model handoff is ready for engineering review."] = (
        "A model handoff is ready for engineering review."
    )
