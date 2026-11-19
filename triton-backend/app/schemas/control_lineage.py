"""Portable MLflow references. Provenance is not a production approval."""

import re
from typing import Literal, Self

from pydantic import field_validator, model_validator

from app.schemas.lineage import Contract, Digest, Identity, Origin, ServingCandidateV1


class MlflowReference(Contract):
    installation_id: Identity
    requested_uri: Identity
    model_uri: Identity
    model_id: Identity | None = None
    training_run_id: Identity | None = None
    registry_name: Identity | None = None
    registry_version: Identity | None = None
    candidate_origin: Origin | None = None

    @field_validator("requested_uri", "model_uri")
    @classmethod
    def model_reference(cls, value: str) -> str:
        if (not value.startswith(("models:/", "runs:/")) or "://" in value
                or any(c in value for c in "?#\\\x00")
                or any(ord(c) < 32 for c in value)
                or any(p in {"", ".", ".."} for p in value.split(":/", 1)[1].split("/"))):
            raise ValueError("Expected a non-secret MLflow model reference")
        return value


    @model_validator(mode="after")
    def pinned(self) -> Self:
        if not re.fullmatch(r"models:/m-[A-Za-z0-9_-]+|runs:/[^/?#\\]+/.+", self.model_uri):
            raise ValueError("Resolved model references must not contain Registry aliases")
        if self.model_id and self.model_uri != "models:/" + self.model_id:
            raise ValueError("Logged model ID disagrees with resolved model URI")
        return self


class ControlLineage(Contract):
    schema_version: Literal[1] = 1
    provider: Literal["mlflow"] = "mlflow"
    origin: Origin
    source: MlflowReference | None = None
    candidate: ServingCandidateV1 | None = None
    previous_serving_model: MlflowReference | None = None
    conversion_run_id: Identity | None = None
    lineage_digest: Digest | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if not re.fullmatch(r"models:/m-[A-Za-z0-9_-]+|runs:/[^/?#\\]+/.+", self.origin.record_id):
            raise ValueError("Origin must identify an exact logged model or run artifact")
        MlflowReference.model_reference(self.origin.record_id)
        if self.candidate is None:
            if any((self.source, self.previous_serving_model, self.conversion_run_id, self.lineage_digest)):
                raise ValueError("Legacy references must not invent derived lineage")
            return self
        if not self.source or not self.lineage_digest or not self.conversion_run_id:
            raise ValueError("Derived models require source, conversion and lineage digest")
        source = self.candidate.derived_from_snapshot
        if (source.kind != "mlflow" or source.origin != Origin(
                installation_id=self.source.installation_id, record_id=self.source.model_uri)
                or self.origin.installation_id != self.candidate.origin.installation_id):
            raise ValueError("Candidate and MLflow origins disagree")
        previous = self.previous_serving_model
        expected = (previous.candidate_origin or Origin(installation_id=previous.installation_id,
                                                       record_id=previous.model_uri)) if previous else None
        if expected != self.candidate.previous_serving_candidate:
            raise ValueError("Predecessor references disagree")
        if not any(a.origin.record_id == self.conversion_run_id for a in self.candidate.attempts):
            raise ValueError("Conversion run is missing from the candidate")
        return self
