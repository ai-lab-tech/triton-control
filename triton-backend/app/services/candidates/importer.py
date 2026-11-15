"""Authorized neutral repository capture, validation and immutable registration."""

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError
from sqlmodel import Session

from app.core.identity import require_user_entity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError, ServiceUnavailableError
from app.repositories import candidate_imports, lineage, teams
from app.repositories import preparation as task_repository
from app.schemas.candidate_import import ImportedCandidate, PreparedImportRequest
from app.schemas.lineage import EngineeringAttempt, InventoryV1, Origin, ServingCandidateV1, Snapshot
from app.services import lineage as identities
from app.services.candidates.artifacts import (
    ArtifactSource,
    ArtifactStore,
    CaptureLimits,
    DirectoryArtifactStore,
    DirectorySource,
    S3ArtifactStore,
    S3Source,
)
from app.services.candidates.validation import partition, validate_layout


def _authorize(
    session: Session,
    claims: dict[str, Any],
    task_id: UUID | None,
    expected_revision: int | None,
    *,
    lock: bool,
    allow_cancelled: bool = False,
) -> int:
    mapped = require_user_entity(session, claims)
    if mapped.id is None:
        raise ForbiddenError("Active member or admin required")
    if task_id is not None:
        task = task_repository.get(session, task_id, lock=lock)
        team_id = task.assigned_team_id
    else:
        task = None
        team_id = teams.INSTALLATION_TEAM_ID
    if lock:
        teams.get(session, team_id, lock=True)
    actor = task_repository.current_user(session, mapped.id)
    if actor is None or not actor.is_active or actor.role.strip().lower() not in {"admin", "member"}:
        raise ForbiddenError("Active member or admin required")
    admin = actor.role.strip().lower() == "admin"
    if not admin and mapped.id not in teams.eligible_user_ids(session, team_id):
        raise ForbiddenError("Current engineering responsibility required")
    if task is not None:
        if task.state == "cancelled" and not allow_cancelled:
            raise ConflictError("Cancelled task cannot accept an import")
        if not admin and task.assignee_user_id != mapped.id:
            raise ForbiddenError("Only the current assignee may import for this task")
        if expected_revision is not None and task.revision != expected_revision:
            raise ConflictError("Preparation task changed; reload before importing")
    return mapped.id


def _candidate(
    request: PreparedImportRequest, inventory: InventoryV1
) -> tuple[ServingCandidateV1, tuple[InventoryV1, ...]]:
    output = identities.inventory_digest(inventory)
    snapshot = request.source_snapshot
    if snapshot is None:
        if request.manual_source_origin is None:
            raise BadRequestError("Manual capture requires its actual source origin")
        snapshot = Snapshot(origin=request.manual_source_origin, kind="manual", inventory=output)
    # This operation really captured and packaged the prepared files. It is not
    # a fabricated training/conversion run, and it does not change their bytes.
    capture = EngineeringAttempt(
        origin=request.capture_origin,
        operation="packaging",
        input_inventory=output,
        output_inventory=output,
        tool="triton-control.neutral-importer/v1",
    )
    try:
        candidate = ServingCandidateV1(
            origin=request.candidate_origin,
            derived_from_snapshot=snapshot,
            attempts=(*request.attempts, capture),
            output_inventory=output,
            previous_serving_candidate=request.previous_serving_candidate,
            plugin_references=request.plugin_references,
        )
    except ValidationError as exc:
        raise BadRequestError("Prepared bytes do not match the source/attempt inventory chain") from exc
    inventories = {identities.inventory_digest(item): item for item in (*request.prior_inventories, inventory)}
    return candidate, tuple(inventories[key] for key in sorted(inventories))


def import_repository(
    session: Session,
    claims: dict[str, Any],
    request: PreparedImportRequest,
    source: ArtifactSource,
    store: ArtifactStore,
    *,
    limits: CaptureLimits | None = None,
    commit: bool = True,
) -> ImportedCandidate:
    """Internal service entry point; providers must be constructed from authorized inputs.

    Default publication commits candidate + receipt together. commit=False joins a
    caller-owned transaction; a rollback may leave an unreferenced stored snapshot,
    which must never be mistaken for an approved candidate/release.
    """
    actor = _authorize(session, claims, request.task_id, request.expected_task_revision, lock=False)
    if not isinstance(source, ArtifactSource) or not isinstance(store, ArtifactStore):
        raise BadRequestError("Exactly one supported artifact source and one store are required")
    if isinstance(source, DirectorySource) and isinstance(store, DirectoryArtifactStore):
        source_root, store_root = source.root.absolute(), store.root.absolute()
        if source_root.is_relative_to(store_root) or store_root.is_relative_to(source_root):
            raise BadRequestError("Source and artifact-store directories must not overlap")
    if isinstance(source, S3Source) and isinstance(store, S3ArtifactStore) and source.bucket == store.bucket:
        output_prefix = store.prefix + "/"
        if output_prefix.startswith(source.prefix) or source.prefix.startswith(output_prefix):
            raise BadRequestError("Source and artifact-store prefixes must not overlap")
    limits = limits or CaptureLimits()
    try:
        with tempfile.TemporaryDirectory(prefix="candidate-import-") as directory:
            staging = Path(directory)
            captured = source.capture(staging, limits)
            inventory, metadata = partition(captured.inventory)
            models = validate_layout(staging, inventory, request.triton_version, request.expected_backends, limits)
            candidate, inventories = _candidate(request, inventory)
            identities.validate_publication(session, candidate, inventories)
            # Recheck claims, assignment, revision and eligibility after potentially
            # slow I/O, before publishing any database/artifact references.
            _authorize(session, claims, request.task_id, request.expected_task_revision, lock=False)
            missing = set(request.missing_evaluation_inputs)
            if request.task_id is not None:
                missing.update(task_repository.get(session, request.task_id).missing_inputs)
            candidate_payload = candidate.model_dump(mode="json")
            candidate_digest = identities.digest(candidate_payload)
            import_digest = identities.digest(
                {
                    "candidate_digest": candidate_digest,
                    "triton_version": request.triton_version,
                    "expected_backends": request.expected_backends,
                    "task_revision": request.expected_task_revision,
                    "inventory": identities.inventory_digest(inventory),
                    "metadata_inventory": identities.inventory_digest(metadata),
                    "models": [m.model_dump() for m in models],
                    "missing_evaluation_inputs": sorted(missing),
                }
            )
            existing = candidate_imports.find(session, candidate.origin.installation_id, candidate.origin.record_id)
            if existing is not None:
                candidate_imports.check(existing, candidate_digest, import_digest, actor, request.task_id)
                result = ImportedCandidate.model_validate(existing.result)
                store.verify(result.artifacts, result.inventory, result.metadata_inventory, limits)
            else:
                created = datetime.utcnow()
                manifest = {
                    "schema_version": 1,
                    "candidate": candidate_payload,
                    "candidate_digest": candidate_digest,
                    "models": [m.model_dump() for m in models],
                    "missing_evaluation_inputs": sorted(missing),
                    "source_receipt": captured.receipt,
                }
                artifacts = store.publish(staging, inventory, metadata, manifest, limits)
                result = ImportedCandidate(
                    candidate=candidate,
                    candidate_digest=candidate_digest,
                    inventory=inventory,
                    metadata_inventory=metadata,
                    models=models,
                    missing_evaluation_inputs=tuple(sorted(missing)),
                    completeness="evaluation_inputs_missing" if missing else "evaluation_inputs_declared",
                    artifacts=artifacts,
                    source_receipt=captured.receipt,
                    owner_user_id=actor,
                    task_id=request.task_id,
                    created_at=created,
                )
            # Keep database locks out of artifact/network I/O. A competing or
            # revoked import may leave a verified but unregistered snapshot; it
            # is never exposed as a candidate and is retained for reconciliation.
            _authorize(session, claims, request.task_id, request.expected_task_revision, lock=True)
            lineage.insert(
                session,
                candidate.origin.installation_id,
                candidate.origin.record_id,
                candidate_digest,
                identities.canonical_bytes(candidate_payload).decode(),
                identities.canonical_bytes([i.model_dump(mode="json") for i in inventories]).decode(),
            )
            candidate_imports.lock_candidate(session, candidate.origin.installation_id, candidate.origin.record_id)
            current = candidate_imports.find(session, candidate.origin.installation_id, candidate.origin.record_id)
            verify_after_commit = False
            if current is None:
                candidate_imports.insert(
                    session,
                    candidate.origin.installation_id,
                    candidate.origin.record_id,
                    candidate_digest,
                    import_digest,
                    actor,
                    request.task_id,
                    result.model_dump(mode="json"),
                )
            else:
                candidate_imports.check(current, candidate_digest, import_digest, actor, request.task_id)
                retained = ImportedCandidate.model_validate(current.result)
                verify_after_commit = retained.artifacts != result.artifacts
                result = retained
            if commit:
                session.commit()
            else:
                session.flush()
            if verify_after_commit:
                store.verify(result.artifacts, result.inventory, result.metadata_inventory, limits)
            return result
    except (BotoCoreError, ClientError, OSError) as exc:
        session.rollback()
        raise ServiceUnavailableError("Candidate artifact storage is unavailable") from exc
    except BaseException:
        session.rollback()
        raise


def get_import(
    session: Session,
    claims: dict[str, Any],
    origin: Origin,
    store: ArtifactStore,
    *,
    limits: CaptureLimits | None = None,
) -> ImportedCandidate:
    """Read a retained import with current account/task authorization and byte verification."""
    row = candidate_imports.find(session, origin.installation_id, origin.record_id)
    if row is None:
        raise NotFoundError("Imported candidate not found")
    result = ImportedCandidate.model_validate(row.result)
    actor = _authorize(session, claims, row.task_id, None, lock=False, allow_cancelled=True)
    user = task_repository.current_user(session, actor)
    if row.task_id is None and actor != row.owner_user_id and (user is None or user.role.strip().lower() != "admin"):
        raise ForbiddenError("Imported candidate access denied")
    store.verify(result.artifacts, result.inventory, result.metadata_inventory, limits or CaptureLimits())
    return result
