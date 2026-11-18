"""Explicit handoffs: one atomic receipt, task, event and recipient notifications."""

from typing import Any, Callable
from uuid import UUID, uuid4

from sqlmodel import Session

from app.core import installation_policy
from app.db.entities import HandoffEntity, HandoffNotificationEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import handoffs as repository
from app.repositories import teams
from app.schemas.handoffs import (
    HandoffReceipt,
    HandoffRequest,
    NotificationDTO,
    ResolvedInput,
    ResolvedRepository,
    S3Input,
    S3RepositoryInput,
    missing_evaluation_inputs,
)
from app.schemas.lineage import InventoryV1, Origin, Snapshot
from app.schemas.preparation import TaskCreate
from app.services import handoff_repositories, handoff_sources, preparation
from app.services.lineage import digest, inventory_digest
from app.services.preparation_settings import effective


def _actor(session: Session, claims: dict[str, Any]) -> int:
    if claims.get("token_use"):
        raise ForbiddenError("An interactive user credential is required for handoffs")
    actor, _, role = preparation._actor(session, claims)
    if role not in {"member", "admin"}:
        raise ForbiddenError("Member or admin role required")
    return actor


def _receipt(session: Session, claims: dict[str, Any], row: HandoffEntity) -> HandoffReceipt:
    task = preparation.get_task(session, claims, row.task_id)
    payload = row.payload
    return HandoffReceipt(id=row.id, task=task, created_at=row.created_at, **payload)


def get(session: Session, claims: dict[str, Any], handoff_id: UUID) -> HandoffReceipt:
    _actor(session, claims)
    return _receipt(session, claims, repository.get(session, handoff_id))


def _selection(source: S3Input | S3RepositoryInput, endpoint: str | None, bucket: str, key: str) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": source.kind, "endpoint": endpoint, "bucket": bucket, "key": key}
    if isinstance(source, S3Input):
        result.update(version_id=source.version_id, expected_digest=source.expected_digest)
    return result


def _selectors(session: Session, request: HandoffRequest) -> dict[str, Any]:
    """Compare intent using authorized locations, not mutable credential/profile labels."""
    payload = request.model_dump(mode="json")
    payload["origin"] = {"installation_id": request.origin.installation_id}
    for name in ("source", "dataset", "baseline"):
        source = getattr(request, name)
        if source is not None:
            config, bucket, key = handoff_sources.authorize(session, source)
            payload[name] = _selection(source, config["s3_endpoint"], bucket, key)
    return payload


def _reuse(
    session: Session, claims: dict[str, Any], row: HandoffEntity, selectors: dict[str, Any],
) -> HandoffReceipt:
    receipt = _receipt(session, claims, row)
    original = receipt.request.model_dump(mode="json")
    original["origin"] = {"installation_id": receipt.request.origin.installation_id}
    for name in ("source", "dataset", "baseline"):
        captured = getattr(receipt, name)
        if captured is not None:
            original[name] = _selection(captured.source, captured.endpoint, captured.bucket, captured.key)
    if digest(original) != digest(selectors):
        raise ConflictError("Handoff source and revision already contain different content; submit a new revision")
    return receipt


def _previous(session: Session, claims: dict[str, Any], request: HandoffRequest) -> None:
    if request.previous_handoff_id is None:
        return
    previous = get(session, claims, request.previous_handoff_id)
    if previous.request.origin.installation_id != request.origin.installation_id:
        raise ConflictError("A handoff revision must retain its origin installation")
    if previous.request.revision == request.revision:
        raise ConflictError("A revised handoff requires a new submission revision")


def submit(session: Session, claims: dict[str, Any], request: HandoffRequest, *,
           checkpoint: Callable[[int], None] | None = None,
           publish: Callable[[Session, UUID], None] | None = None) -> HandoffReceipt:
    installation_policy.require("development_jobs")
    try:
        _actor(session, claims)
        _previous(session, claims, request)
        content = digest(request.model_dump(mode="json"))
        selectors = _selectors(session, request)
        selected = selectors["source"]
        version = request.source.version_id if isinstance(request.source, S3Input) else None
        existing = repository.find_source(
            session, request.origin.installation_id, request.revision,
            selected["endpoint"], selected["bucket"], selected["key"], version, request.source.kind,
        )
        if existing is not None:
            return _reuse(session, claims, existing, selectors)
        # No database write/row lock across provider reads. Each mutable object is
        # resolved once to a non-null version ID; retries return the stored receipt.
        options: dict[str, Any] = {"checkpoint": checkpoint} if checkpoint else {}
        source = (
            handoff_repositories.resolve(session, request.source, **options)
            if isinstance(request.source, S3RepositoryInput)
            else handoff_sources.resolve(session, request.source, **options)
        )
        dataset = handoff_sources.resolve(session, request.dataset, **options) if request.dataset else None
        baseline = handoff_sources.resolve(session, request.baseline, **options) if request.baseline else None
        if checkpoint:
            checkpoint(0)
        artifacts = source.files if isinstance(source, ResolvedRepository) else [source]
        total_bytes = sum(item.file.size for item in [*artifacts, dataset, baseline] if item is not None)
        if total_bytes > effective(session).max_input_gib * 1024**3:
            raise BadRequestError("Combined inputs exceed the configured workspace input size limit")
        teams.get(session, teams.INSTALLATION_TEAM_ID, lock=True)
        _actor(session, claims)
        _previous(session, claims, request)
        for item in (source, dataset, baseline):
            if item is not None:
                config, bucket, key = handoff_sources.authorize(session, item.source)
                if (config["s3_endpoint"], bucket, key) != (item.endpoint, item.bucket, item.key):
                    raise ConflictError("Source profile changed during submission; retry")
        # Concurrent unpinned submissions must reuse the first committed capture,
        # even if the object changed between their HEAD requests.
        existing = repository.find_source(
            session, request.origin.installation_id, request.revision,
            source.endpoint, source.bucket, source.key, version, request.source.kind,
        ) or repository.find_source(
            session, request.origin.installation_id, request.revision,
            source.endpoint, source.bucket, source.key,
            source.version_id if isinstance(source, ResolvedInput) else None, request.source.kind,
        )
        if existing is not None:
            receipt = _reuse(session, claims, existing, selectors)
            session.commit()
            return receipt
        identity = digest({
            "installation_id": request.origin.installation_id, "revision": request.revision,
            "kind": request.source.kind, "endpoint": source.endpoint, "bucket": source.bucket, "key": source.key,
            "version_id": source.version_id if isinstance(source, ResolvedInput) else None,
        })
        missing = missing_evaluation_inputs(dataset, baseline, request.expected_behavior, request.acceptance_criteria)
        snapshot_id = uuid4()
        task = preparation.create_task(
            session,
            claims,
            TaskCreate(title=request.title, source_snapshot_id=snapshot_id, missing_inputs=missing),
            commit=False,
        )
        snapshot = Snapshot(
            origin=Origin(installation_id=request.origin.installation_id, record_id=str(snapshot_id)),
            kind="manual",
            inventory=inventory_digest(InventoryV1(files=(source.file,) if isinstance(source, ResolvedInput)
                                                 else tuple(item.file for item in source.files))),
        )

        def value(item: ResolvedInput | ResolvedRepository | None) -> dict[str, Any] | None:
            return item.model_dump(mode="json") if item else None

        row = HandoffEntity(
            identity_digest=identity,
            request_digest=content,
            task_id=task.id,
            payload={
                "request_digest": content,
                "request": request.model_dump(mode="json"),
                "source_snapshot": snapshot.model_dump(mode="json"),
                "source": value(source),
                "dataset": value(dataset),
                "baseline": value(baseline),
            },
        )
        repository.add(session, row, teams.eligible_user_ids(session, teams.INSTALLATION_TEAM_ID))
        if publish:
            publish(session, row.id)
        session.commit()
        return _receipt(session, claims, row)
    except Exception:
        session.rollback()
        raise


def _notification(row: HandoffNotificationEntity) -> NotificationDTO:
    return NotificationDTO(id=row.id, handoff_id=row.handoff_id, created_at=row.created_at, read_at=row.read_at)


def notifications(session: Session, claims: dict[str, Any], limit: int, offset: int) -> list[NotificationDTO]:
    actor = _actor(session, claims)
    return [_notification(row) for row in repository.notifications(session, actor, limit, offset)]


def read_notification(session: Session, claims: dict[str, Any], notification_id: UUID) -> NotificationDTO:
    return _notification(repository.mark_read(session, _actor(session, claims), notification_id))
