"""Offline admission for a proposed release using installation-owned trust."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from app.core.canonical import digest
from app.schemas.gate_evidence import EvidenceArchive, GatePolicy
from app.schemas.serving_metadata import ServingRelease
from app.schemas.validation import EvidenceEnvelope, HardwareRuntime, ValidationManifest, ValidationReport
from app.services.contract_io import MetadataError, parse_json_document
from app.services.validation import check_evidence_consistency, check_release_binding, document_digest

POLICY_FILE = "TRITON_GATE_POLICY_FILE"
MAX_POLICY_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024


def signing_message(envelope: EvidenceEnvelope) -> bytes:
    """Versioned, domain-separated bytes for an independent validation issuer."""
    return f"triton-control/validation-evidence/v1\n{document_digest(envelope)}".encode("ascii")


def package_evidence(
    manifest: ValidationManifest, report: ValidationReport, envelope: EvidenceEnvelope, signature: str,
) -> bytes:
    """Serialize independently signed evidence for retention alongside a release."""
    archive = EvidenceArchive(manifest=manifest, report=report, envelope=envelope, signature=signature)
    raw = archive.model_dump_json(by_alias=True).encode("utf-8")
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise MetadataError("evidence-archive-too-large")
    return raw


def _load_policy() -> GatePolicy:
    path = os.environ.get(POLICY_FILE)
    if not path:
        raise MetadataError("gate-policy-not-configured")
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
        if len(raw) <= MAX_POLICY_BYTES:
            return GatePolicy.model_validate(parse_json_document(raw))
    except (OSError, MetadataError, ValidationError):
        pass
    raise MetadataError("invalid-gate-policy")


def verify_release_evidence(
    release: ServingRelease,
    archive_bytes: bytes,
    *,
    target: HardwareRuntime,
    now: datetime | None = None,
) -> str:
    """Verify retained evidence and return the binding for independent approval."""
    policy = _load_policy()
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise MetadataError("evidence-archive-too-large")
    try:
        archive = EvidenceArchive.model_validate(parse_json_document(archive_bytes))
    except (MetadataError, ValidationError):
        raise MetadataError("invalid-evidence-archive") from None

    envelope = archive.envelope
    key = policy.issuers.get(envelope.issuer)
    if key is None:
        raise MetadataError("untrusted-evidence-issuer")
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(key)).verify(
            bytes.fromhex(archive.signature), signing_message(envelope)
        )
    except InvalidSignature:
        raise MetadataError("invalid-evidence-signature") from None

    check_release_binding(release, archive.manifest, envelope)
    check_evidence_consistency(
        archive.manifest, archive.report, envelope,
        target=target, policy=policy.identity, now=now if now is not None else datetime.now(timezone.utc),
    )
    if envelope.valid_until - envelope.issued_at > timedelta(seconds=policy.max_validity_seconds):
        raise MetadataError("evidence-validity-exceeds-policy")
    criteria = {criterion.metric: criterion for criterion in archive.manifest.criteria}
    if archive.manifest.evaluator != policy.evaluator or any(
        criteria.get(required.metric) != required for required in policy.required_criteria
    ):
        raise MetadataError("required-validation-checks-missing")

    return digest({
        "release": release.model_dump(mode="json", by_alias=True),
        "archive": archive.model_dump(mode="json", by_alias=True),
        "policy": policy.model_dump(mode="json", by_alias=True),
        "target": target.model_dump(mode="json", by_alias=True),
    })


def admit_release(
    release: ServingRelease, archive_bytes: bytes, *, target: HardwareRuntime,
    approved_binding: str, now: datetime | None = None,
) -> str:
    """Reverify current policy and evidence before a caller changes a workload."""
    binding = verify_release_evidence(release, archive_bytes, target=target, now=now)
    if approved_binding != binding:
        raise MetadataError("gate-evidence-approval-invalidated")
    return binding
