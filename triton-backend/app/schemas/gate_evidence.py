"""Local trust policy and portable signed validation evidence."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from app.schemas.lineage import Digest, InventoryV1
from app.schemas.serving_metadata import ArtifactSubjects, Contract, Count, Name
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


class SecurityPolicy(Contract):
    issuers: dict[Name, PublicKey] = Field(min_length=1, max_length=100)
    bandit_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    trivy_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    max_validity_seconds: int = Field(strict=True, gt=0, le=365 * 24 * 3600)
    max_code_findings: Count
    max_image_findings: Count
    min_packages: Count = Field(ge=1)
    approved_model_inventories: tuple[Digest, ...] = Field(min_length=1, max_length=10000)


class SecurityReport(Contract):
    suite: Literal["triton-security-v1"]
    issuer: Name
    subjects: ArtifactSubjects
    issued_at: AwareDatetime
    bandit_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    trivy_version: str
    code_findings: Count
    image_findings: Count
    packages: Count
    code_report: Digest
    image_report: Digest
    model_inventory: InventoryV1


class SignedSecurityReport(Contract):
    report: SecurityReport
    signature: Signature


class GatePolicy(Contract):
    security: SecurityPolicy | None = None
    identity: PolicyIdentity
    issuers: dict[Name, PublicKey] = Field(min_length=1, max_length=100)
    evaluator: EvaluatorIdentity
    required_criteria: tuple[Criterion, ...] = Field(min_length=1, max_length=100)
    max_validity_seconds: int = Field(strict=True, gt=0, le=365 * 24 * 3600)


class EvidenceArchive(Contract):
    security: SignedSecurityReport | None = None
    manifest: ValidationManifest
    report: ValidationReport
    envelope: EvidenceEnvelope
    signature: Signature
