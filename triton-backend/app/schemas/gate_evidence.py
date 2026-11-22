"""Local trust policy and portable signed validation evidence."""

from typing import Annotated

from pydantic import Field

from app.schemas.serving_metadata import Contract, Name
from app.schemas.validation import (
    Criterion,
    EvaluatorIdentity,
    EvidenceEnvelope,
    PolicyIdentity,
    ValidationManifest,
    ValidationReport,
)

PublicKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Signature = Annotated[str, Field(pattern=r"^[0-9a-f]{128}$")]


class GatePolicy(Contract):
    identity: PolicyIdentity
    issuers: dict[Name, PublicKey] = Field(min_length=1, max_length=100)
    evaluator: EvaluatorIdentity
    required_criteria: tuple[Criterion, ...] = Field(min_length=1, max_length=100)
    max_validity_seconds: int = Field(strict=True, gt=0, le=365 * 24 * 3600)


class EvidenceArchive(Contract):
    manifest: ValidationManifest
    report: ValidationReport
    envelope: EvidenceEnvelope
    signature: Signature
