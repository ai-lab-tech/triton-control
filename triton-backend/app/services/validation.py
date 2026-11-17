"""Offline S07-A identity/consistency checks, not evidence authentication or approval."""

from datetime import datetime

from app.schemas.serving_metadata import ArtifactSubjects, ServingRelease
from app.schemas.validation import (
    API_VERSION,
    DOCUMENT_MODELS,
    Document,
    EvidenceEnvelope,
    HardwareRuntime,
    PolicyIdentity,
    ValidationManifest,
    ValidationReport,
)
from app.services.contract_io import Format, MetadataError, ValidatedContract, load_contract
from app.services.lineage import digest

ValidatedDocument = ValidatedContract[Document]


def document_digest(document: Document) -> str:
    return digest(document.model_dump(mode="json", by_alias=True))


def load_validation(raw: bytes, *, format: Format = "auto") -> ValidatedDocument:
    return load_contract(
        raw, format=format, api_version=API_VERSION, models=DOCUMENT_MODELS,
        schema_package="app.services.validation_contracts",
    )


def artifact_subjects(manifest: ValidationManifest) -> ArtifactSubjects:
    materializer = manifest.repository.materializer_image
    return ArtifactSubjects.model_validate({
        "image": manifest.image.split("@", 1)[1],
        "inventoryDigest": manifest.repository.inventory_digest,
        "preparedInventoryDigest": manifest.repository.prepared_inventory_digest,
        "materializerImage": materializer.split("@", 1)[1] if materializer else None,
        "sourceCode": manifest.source_code.digest if manifest.source_code else None,
        "validationInput": document_digest(manifest),
    })


def check_report(manifest: ValidationManifest, report: ValidationReport) -> None:
    """Check a returned report against its entire input. Does not authenticate the runner."""
    if report.manifest_digest != document_digest(manifest) or report.subjects != artifact_subjects(manifest):
        raise MetadataError("stale-or-mismatched-subjects")
    if report.evaluator != manifest.evaluator:
        raise MetadataError("evaluator-mismatch")
    supported = report.measured_target in manifest.supported_targets
    if report.status in {"passed", "failed"} and not supported:
        raise MetadataError("unsupported-measured-target")
    if report.status == "unsupported" and supported:
        raise MetadataError("contradictory-unsupported-result")
    criteria = {c.metric: c for c in manifest.criteria}
    for metric in report.metrics:
        if criteria.get(metric.criterion.metric) != metric.criterion:
            raise MetadataError("criteria-mismatch")
    if report.status in {"passed", "failed"} and {m.criterion.metric for m in report.metrics} != set(criteria):
        raise MetadataError("missing-metrics")


def check_evidence_consistency(
    manifest: ValidationManifest, report: ValidationReport, envelope: EvidenceEnvelope, *,
    target: HardwareRuntime, policy: PolicyIdentity, now: datetime,
) -> None:
    """Necessary compatibility checks only. S07-C must authenticate issuer and enforce trust.

    Success is NOT release readiness or deployment approval. No replicas are accepted:
    capacity and fleet performance are independent of single-replica artifact evidence.
    """
    check_report(manifest, report)
    if report.status != "passed":
        raise MetadataError("validation-not-passed")
    if envelope.subjects != report.subjects or envelope.report.digest != document_digest(report):
        raise MetadataError("evidence-report-mismatch")
    if envelope.policy != policy:
        raise MetadataError("policy-mismatch")
    if not any(t.name == manifest.evaluator.name and t.version == manifest.evaluator.version for t in envelope.tools):
        raise MetadataError("evaluator-tool-version-missing")
    if now.tzinfo is None or now.utcoffset() is None:
        raise MetadataError("timezone-required")
    if not report.completed_at <= envelope.issued_at <= now < envelope.valid_until:
        raise MetadataError("evidence-outside-validity")
    if target not in manifest.supported_targets:
        raise MetadataError("unsupported-target")
    if target != report.measured_target:
        raise MetadataError("unmeasured-target")


def check_release_binding(
    release: ServingRelease, manifest: ValidationManifest, envelope: EvidenceEnvelope,
) -> None:
    """Bridge S08-A artifact/evidence references without a release -> evidence hash cycle."""
    spec = release.spec
    if (
        spec.image != manifest.image or spec.repository != manifest.repository
        or spec.models != manifest.startup.models
        or spec.resources_per_replica != manifest.startup.resources_per_replica
        or spec.validation.subjects != artifact_subjects(manifest)
        or envelope.subjects != artifact_subjects(manifest)
        or spec.validation.digest != document_digest(envelope)
    ):
        raise MetadataError("release-validation-mismatch")
