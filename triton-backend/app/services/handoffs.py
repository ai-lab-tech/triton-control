"""Explicit handoffs: one atomic receipt, task, event and recipient notifications."""

from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session

from app.db.entities import HandoffEntity, HandoffNotificationEntity
from app.exceptions import ConflictError, ForbiddenError
from app.repositories import handoffs as repository
from app.repositories import teams
from app.schemas.handoffs import HandoffReceipt, HandoffRequest, NotificationDTO, ResolvedInput
from app.schemas.lineage import InventoryV1, Origin, Snapshot
from app.schemas.preparation import TaskCreate
from app.services import handoff_sources, preparation
from app.services.lineage import digest, inventory_digest


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


def _inputs(request: HandoffRequest) -> list[Any]:
    return [item for item in (request.source, request.dataset, request.baseline) if item is not None]


def submit(session: Session, claims: dict[str, Any], request: HandoffRequest) -> HandoffReceipt:
    try:
        _actor(session, claims)
        identity = digest({"origin": request.origin.model_dump(), "revision": request.revision})
        content = digest(request.model_dump(mode="json"))
        for source in _inputs(request):
            handoff_sources.authorize(session, source)
        existing = repository.find(session, identity)
        if existing is not None:
            receipt = _receipt(session, claims, existing)
            if receipt.request_digest != content:
                raise ConflictError("Handoff identity already contains different content; submit a new revision")
            return receipt
        # No database write/row lock across provider reads. Each mutable object is
        # resolved once to a non-null version ID; retries return the stored receipt.
        source = handoff_sources.resolve(session, request.source)
        dataset = handoff_sources.resolve(session, request.dataset) if request.dataset else None
        baseline = handoff_sources.resolve(session, request.baseline) if request.baseline else None
        teams.get(session, teams.INSTALLATION_TEAM_ID, lock=True)
        _actor(session, claims)
        for item in (source, dataset, baseline):
            if item is not None:
                config, bucket, key = handoff_sources.authorize(session, item.source)
                if (config["s3_endpoint"], bucket, key) != (item.endpoint, item.bucket, item.key):
                    raise ConflictError("Source profile changed during submission; retry")
        existing = repository.find(session, identity)
        if existing is not None:
            receipt = _receipt(session, claims, existing)
            if receipt.request_digest != content:
                raise ConflictError("Handoff identity already contains different content; submit a new revision")
            session.commit()
            return receipt
        missing = [
            name
            for name, value in (
                ("evaluation_dataset", dataset),
                ("baseline", baseline),
                ("expected_behavior", request.expected_behavior),
                ("acceptance_criteria", request.acceptance_criteria),
            )
            if value is None
        ]
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
            inventory=inventory_digest(InventoryV1(files=(source.file,))),
        )

        def value(item: ResolvedInput | None) -> dict[str, Any] | None:
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
