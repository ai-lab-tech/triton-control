"""Version one tool-independent provenance contracts. No I/O."""

from typing import Annotated, Literal, Self
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Identity = Annotated[str, Field(min_length=1, max_length=256)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Origin(Contract):
    installation_id: Identity
    record_id: Identity


class FileEntry(Contract):
    path: str
    size: Annotated[int, Field(strict=True, ge=0)]
    digest: Digest

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        if (
            not self.path
            or self.path != normalize("NFC", self.path)
            or any(part in {"", ".", ".."} for part in self.path.split("/"))
            or "\\" in self.path
            or ":" in self.path
            or any(ord(c) < 32 for c in self.path)
        ):
            raise ValueError("Expected a normalized relative POSIX file path")
        return self


class InventoryV1(Contract):
    schema_version: Literal[1] = 1
    files: tuple[FileEntry, ...]

    @model_validator(mode="after")
    def unique_paths(self) -> Self:
        if len({entry.path for entry in self.files}) != len(self.files):
            raise ValueError("Duplicate inventory path")
        return self


class Snapshot(Contract):
    origin: Origin
    kind: Literal["manual", "git", "hf", "mlflow"]
    inventory: Digest


class EngineeringAttempt(Contract):
    origin: Origin
    operation: Literal["packaging", "conversion", "optimization", "validation"]
    input_inventory: Digest
    output_inventory: Digest
    tool: str | None = None


class PluginReference(Contract):
    provider: Literal["mlflow", "hf", "generator"]
    installation_id: Identity
    record_id: Identity


class ServingCandidateV1(Contract):
    schema_version: Literal[1] = 1
    origin: Origin
    derived_from_snapshot: Snapshot
    attempts: tuple[EngineeringAttempt, ...] = Field(min_length=1)
    output_inventory: Digest
    previous_serving_candidate: Origin | None = None
    plugin_references: tuple[PluginReference, ...] = ()

    @model_validator(mode="after")
    def consistent_attempts(self) -> Self:
        current = self.derived_from_snapshot.inventory
        seen = set()
        for attempt in self.attempts:
            key = (attempt.origin.installation_id, attempt.origin.record_id)
            if key in seen or attempt.input_inventory != current:
                raise ValueError("Attempts must be unique and form an ordered inventory chain")
            seen.add(key)
            current = attempt.output_inventory
        if current != self.output_inventory:
            raise ValueError("Candidate output must match the final attempt")
        if self.previous_serving_candidate == self.origin:
            raise ValueError("Candidate cannot be its own predecessor")
        return self


class ReleaseLineageV1(Contract):
    schema_version: Literal[1] = 1
    origin: Origin
    candidate: Origin
    candidate_digest: Digest
    runtime_image: Annotated[str, Field(pattern=r"^[^\s@]+@sha256:[0-9a-f]{64}$")]
    replaces_release: Origin | None = None
