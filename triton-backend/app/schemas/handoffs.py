"""Versioned explicit handoff contracts. No storage or authorization I/O."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.lineage import Digest, FileEntry, Origin, Snapshot
from app.schemas.preparation import Request, TaskDTO


class S3Input(Request):
    kind: Literal["s3"] = "s3"
    profile_id: int = Field(strict=True, ge=1)
    path: str = Field(min_length=1, max_length=1024, pattern=r"^[^\x00-\x1f]+$")
    version_id: str | None = Field(default=None, min_length=1, max_length=1024)
    expected_digest: Digest | None = None


class HandoffRequest(Request):
    schema_version: Literal[1] = 1
    origin: Origin
    previous_handoff_id: UUID | None = None
    revision: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=200)
    source: S3Input
    dataset: S3Input | None = None
    baseline: S3Input | None = None
    expected_behavior: str | None = Field(default=None, min_length=1, max_length=8000)
    acceptance_criteria: str | None = Field(default=None, min_length=1, max_length=8000)


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
    version_id: str
    file: FileEntry


class HandoffReceipt(Request):
    schema_version: Literal[1] = 1
    id: UUID
    request_digest: Digest
    request: HandoffRequest
    source_snapshot: Snapshot
    source: ResolvedInput
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
