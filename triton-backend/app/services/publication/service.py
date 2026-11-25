"""Offline publication using credentials supplied by the authorized publisher."""

from pathlib import Path
from typing import Any

from app.core.canonical import canonical_bytes, digest, inventory_digest
from app.schemas.gate_evidence import EvidenceArchive
from app.schemas.lineage import InventoryV1, ReleaseLineageV1, ServingCandidateV1
from app.schemas.materialization import MaterializationPlan
from app.schemas.publication import PublicationReceipt
from app.schemas.serving_metadata import ServingRelease, ServingTarget
from app.services.candidates.artifacts import CaptureLimits
from app.services.contract_io import parse_json_document
from app.services.gate_evidence import MAX_ARCHIVE_BYTES
from app.services.publication.models import promote_models
from app.services.publication.storage import MetadataStore, checksum
from app.services.serving_metadata import load_metadata
from app.services.validation import check_release_binding, check_report, document_digest


def read_file(path: Path, limit: int = MAX_ARCHIVE_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"Publication input too large: {path.name}")
    return data


def publish(
    bundle: Path, store: MetadataStore, client: Any, bucket: str, prefix: str,
    limits: CaptureLimits, previous: str | None,
) -> str:
    original = load_metadata(read_file(bundle / "release.json"))
    target_input = load_metadata(read_file(bundle / "target.json"))
    release, target = original.document, target_input.document
    if not isinstance(release, ServingRelease) or not isinstance(target, ServingTarget):
        raise ValueError("Expected ServingRelease and ServingTarget")
    if target.spec.release.digest != original.digest:
        raise ValueError("Target does not select the input release")
    spec = release.spec
    plan = MaterializationPlan.model_validate_json(read_file(bundle / "plan.json"))
    candidate = ServingCandidateV1.model_validate_json(read_file(bundle / "candidate.json"))
    lineage = spec.lineage
    candidate_data = candidate.model_dump(mode="json")
    if (candidate.origin != lineage.candidate or digest(candidate_data) != lineage.candidate_digest
            or candidate.output_inventory != inventory_digest(plan.source_inventory)
            or spec.repository.inventory_digest != candidate.output_inventory
            or spec.repository.prepared_inventory_digest != inventory_digest(plan.prepared_inventory)
            or spec.repository.materializer_image != plan.materializer_image):
        raise ValueError("Candidate, plan and release inputs differ")
    archive_bytes = read_file(bundle / "validation-archive.json")
    archive = EvidenceArchive.model_validate(parse_json_document(archive_bytes))
    if archive.manifest.repository != spec.repository:
        raise ValueError("Source repository differs from validation input")
    check_release_binding(release, archive.manifest, archive.envelope)
    check_report(archive.manifest, archive.report)
    if archive.report.status != "passed" or archive.envelope.report.digest != document_digest(archive.report):
        raise ValueError("Archived validation report does not match passing evidence")

    artifacts: dict[str, bytes] = {}

    def retain(data: bytes) -> str:
        key = "artifact-" + checksum(data)[7:] + ".json"
        artifacts[key] = data
        return key

    release_data = release.model_dump(mode="json", by_alias=True)
    output = release_data["spec"]
    lineage_bytes = read_file(bundle / "lineage.json")
    if (checksum(lineage_bytes) != spec.lineage_manifest.digest
            or ReleaseLineageV1.model_validate(parse_json_document(lineage_bytes)) != lineage):
        raise ValueError("Lineage manifest digest mismatch")
    output["lineageManifest"]["uri"] = retain(lineage_bytes)
    output["validation"]["uri"] = retain(canonical_bytes(archive.envelope.model_dump(mode="json", by_alias=True)))
    if spec.security:
        security = read_file(bundle / "security.json")
        if digest(parse_json_document(security)) != spec.security.digest:
            raise ValueError("Security evidence digest mismatch")
        output["security"]["uri"] = retain(security)

    inventories = [plan.source_inventory, plan.prepared_inventory]
    for file in sorted(bundle.glob("inventory-*.json")):
        inventories.append(InventoryV1.model_validate_json(read_file(file)))
    index = {inventory_digest(item): retain(canonical_bytes(item.model_dump(mode="json"))) for item in inventories}
    needed = {candidate.derived_from_snapshot.inventory}
    for attempt in candidate.attempts:
        needed.update((attempt.input_inventory, attempt.output_inventory))
    if not needed <= index.keys():
        raise ValueError("Missing attempt inventory")

    destination, promoted = promote_models(client, spec.repository.uri, bucket, prefix, plan, limits)
    output["repository"]["uri"] = destination
    published = load_metadata(canonical_bytes(release_data))
    release_key = f"release-{release.metadata.name}.{release.metadata.release}.json"
    target_data = target.model_dump(mode="json", by_alias=True)
    target_data["spec"]["release"] = {"uri": release_key, "digest": published.digest}
    pointer = load_metadata(canonical_bytes(target_data))
    receipt = {
        "schema_version": 1, "origin_release": retain(original.canonical), "release_digest": published.digest,
        "candidate": retain(canonical_bytes(candidate_data)), "inventories": index,
        "origin_plan": retain(canonical_bytes(plan.model_dump(mode="json"))),
        "published_plan": retain(canonical_bytes(promoted.model_dump(mode="json"))),
        "validation_archive": retain(archive_bytes), "target_digest": pointer.digest,
    }
    receipt_bytes = canonical_bytes(PublicationReceipt.model_validate(receipt).model_dump(mode="json"))
    for key, data in artifacts.items():
        store.write(key, data)
    identity = {key: value for key, value in receipt.items() if key != "target_digest"}
    store.write("identity-" + release_key, canonical_bytes(identity))
    origin_key = digest(lineage.origin.model_dump(mode="json"))[7:]
    store.write(f"origin-{origin_key}.json", canonical_bytes(published.digest))
    store.write(release_key, published.canonical)
    store.write("receipt-" + pointer.digest[7:] + ".json", receipt_bytes)
    store.write_target(f"target-{target.metadata.name}.json", pointer.canonical, previous)
    return pointer.digest
