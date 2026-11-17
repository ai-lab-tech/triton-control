"""S07-A immutable validation contracts; no I/O or evaluator execution."""

import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal, Protocol, Self

from pydantic import AfterValidator, AwareDatetime, BeforeValidator, Field, StrictBool, model_validator

from app.schemas.lineage import Digest
from app.schemas.serving_metadata import (
    ArtifactSubjects,
    Contract,
    Image,
    Name,
    Reference,
    Repository,
    Resources,
    StartupModel,
)

API_VERSION = "validation.triton-control/v1alpha1"
Version = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")]
# Decimal strings keep metrics exact and canonical hashes independent of float encoding.
Number = Annotated[str, Field(max_length=80, pattern=r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")]

def timestamp_input(value: object) -> object:
    """Accept aware Python datetimes or explicit RFC 3339 wire timestamps."""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?([Zz]|[+-][0-9]{2}:[0-9]{2})",
        value,
    ):
        raise ValueError("Expected a timezone-aware RFC 3339 timestamp")
    return value


def utc_timestamp(value: datetime) -> datetime:
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        raise ValueError("Timestamp is outside the supported UTC range") from None


Timestamp = Annotated[AwareDatetime, BeforeValidator(timestamp_input), AfterValidator(utc_timestamp)]


class EvaluatorIdentity(Contract):
    name: Name
    version: Version
    image: Image
    code: Reference


class HardwareRuntime(Contract):
    """Exact supported profiles in v1alpha1; no inferred GPU/driver compatibility."""
    architecture: Literal["amd64", "arm64"]
    operating_system: Literal["linux"]
    cpu_model: Version
    gpu_model: Version | None = None
    gpu_count: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
    driver_version: Version | None = None
    cuda_version: Version | None = None
    triton_version: Version

    @model_validator(mode="after")
    def consistent_gpu(self) -> Self:
        gpu_fields = (self.gpu_model, self.driver_version, self.cuda_version)
        if (self.gpu_count == 0 and any(gpu_fields)) or (self.gpu_count > 0 and not all(gpu_fields)):
            raise ValueError("GPU profile requires explicit model, driver and CUDA, or none for CPU")
        return self


class StartupConfiguration(Contract):
    models: tuple[StartupModel, ...] = Field(min_length=1, max_length=100)
    configuration: Reference
    resources_per_replica: Resources

    @model_validator(mode="after")
    def unique_models(self) -> Self:
        if len({model.name for model in self.models}) != len(self.models):
            raise ValueError("Duplicate startup model")
        return self


class Criterion(Contract):
    metric: Name
    category: Literal["correctness", "performance"]
    unit: Name
    operator: Literal["gte", "lte", "eq"]
    threshold: Number

    def accepts(self, value: str) -> bool:
        actual, threshold = Decimal(value), Decimal(self.threshold)
        if self.operator == "gte":
            return actual >= threshold
        if self.operator == "lte":
            return actual <= threshold
        return actual == threshold


class ValidationManifest(Contract):
    api_version: Literal["validation.triton-control/v1alpha1"]
    kind: Literal["ValidationManifest"]
    image: Image
    repository: Repository
    source_code: Reference | None = None
    dataset: Reference
    expected_outputs: Reference
    evaluator: EvaluatorIdentity
    criteria: tuple[Criterion, ...] = Field(min_length=1, max_length=100)
    startup: StartupConfiguration
    supported_targets: tuple[HardwareRuntime, ...] = Field(min_length=1, max_length=100)
    performance_scope: Literal["single-replica"] = "single-replica"

    @model_validator(mode="after")
    def consistent_manifest(self) -> Self:
        if len({c.metric for c in self.criteria}) != len(self.criteria):
            raise ValueError("Duplicate metric criterion")
        if not any(c.category == "correctness" for c in self.criteria):
            raise ValueError("At least one correctness criterion is required")
        if len(set(self.supported_targets)) != len(self.supported_targets):
            raise ValueError("Duplicate target profile")
        if any(t.gpu_count != self.startup.resources_per_replica.gpu_count for t in self.supported_targets):
            raise ValueError("Target GPU counts must match per-replica resources")
        return self


class MetricResult(Contract):
    criterion: Criterion
    value: Number
    passed: StrictBool

    @model_validator(mode="after")
    def measured_result(self) -> Self:
        if self.passed != self.criterion.accepts(self.value):
            raise ValueError("Passed flag contradicts measured metric and threshold")
        return self


class EvaluationError(Contract):
    code: Literal[
        "unsupported-target", "runner-failure", "missing-metric", "artifact-mismatch",
        "evaluation-failure", "timeout", "cancelled",
    ]
    # Error text/logs are stored separately; never embed credentials/provider errors here.
    diagnostic: Reference | None = None


class ValidationReport(Contract):
    api_version: Literal["validation.triton-control/v1alpha1"]
    kind: Literal["ValidationReport"]
    manifest_digest: Digest
    evaluator: EvaluatorIdentity
    subjects: ArtifactSubjects
    measured_target: HardwareRuntime | None
    started_at: Timestamp
    completed_at: Timestamp
    status: Literal["passed", "failed", "error", "unsupported"]
    metrics: tuple[MetricResult, ...] = Field(max_length=100)
    errors: tuple[EvaluationError, ...] = Field(max_length=100)
    performance_scope: Literal["single-replica"] = "single-replica"

    @model_validator(mode="after")
    def consistent_report(self) -> Self:
        if self.measured_target is None and (self.status != "error" or self.metrics):
            raise ValueError("Only an unmeasured error may omit observed hardware")
        if self.completed_at < self.started_at:
            raise ValueError("Report timestamps are out of order")
        if self.subjects.validation_input != self.manifest_digest:
            raise ValueError("Validation input must bind the complete manifest")
        if len({m.criterion.metric for m in self.metrics}) != len(self.metrics):
            raise ValueError("Duplicate metric result")
        if self.status in {"passed", "failed"}:
            if self.errors or not self.metrics or (self.status == "passed") != all(m.passed for m in self.metrics):
                raise ValueError("Report status contradicts results")
        elif not self.errors:
            raise ValueError("Incomplete evaluation requires an explicit error")
        if self.status == "unsupported" and not any(e.code == "unsupported-target" for e in self.errors):
            raise ValueError("Unsupported result requires a target error")
        return self


class PolicyIdentity(Contract):
    name: Name
    version: Version


class ToolVersion(Contract):
    name: Name
    version: Version


class EvidenceEnvelope(Contract):
    api_version: Literal["validation.triton-control/v1alpha1"]
    kind: Literal["EvidenceEnvelope"]
    issuer: Name
    policy: PolicyIdentity
    tools: tuple[ToolVersion, ...] = Field(min_length=1, max_length=100)
    subjects: ArtifactSubjects
    report: Reference
    issued_at: Timestamp
    valid_until: Timestamp

    @model_validator(mode="after")
    def consistent_envelope(self) -> Self:
        if self.valid_until <= self.issued_at:
            raise ValueError("Evidence validity must be positive and bounded")
        if self.subjects.validation_input is None:
            raise ValueError("Evidence requires the complete validation manifest identity")
        if len({t.name for t in self.tools}) != len(self.tools):
            raise ValueError("Duplicate tool name")
        return self


class Evaluator(Protocol):
    """Implemented by an isolated runner in S07-B, never called by the API process.

    The runner serializes the returned report as report.json with wire aliases.
    Failures produce an error/unsupported report, never a passing empty result.
    """
    def evaluate(self, manifest: ValidationManifest) -> ValidationReport: ...


Document = ValidationManifest | ValidationReport | EvidenceEnvelope
DOCUMENT_MODELS: dict[str, type[ValidationManifest] | type[ValidationReport] | type[EvidenceEnvelope]] = {
    "ValidationManifest": ValidationManifest,
    "ValidationReport": ValidationReport,
    "EvidenceEnvelope": EvidenceEnvelope,
}
