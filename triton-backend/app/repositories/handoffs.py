"""Handoff receipt and recipient-scoped notification persistence."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, select

from app.db.entities import HandoffEntity, HandoffNotificationEntity
from app.exceptions import NotFoundError


def find_source(
    session: Session, installation: str, revision: str, endpoint: str, bucket: str, key: str,
    version: str | None, kind: str = "s3", *, model_name: str | None = None, model_versions: tuple[str, ...] = (),
) -> HandoffEntity | None:
    """Match captured source identity, including receipts written before source hashing.

    An unpinned selector reuses its first captured receipt; an explicit version
    matches the captured version. Never infer identity from a profile/record label.
    """
    payload = HandoffEntity.payload
    statement = select(HandoffEntity).where(
        payload["request"]["origin"]["installation_id"].as_string() == installation,
        payload["request"]["revision"].as_string() == revision,
        payload["source"]["endpoint"].as_string() == endpoint,
        payload["source"]["bucket"].as_string() == bucket,
        payload["source"]["key"].as_string() == key,
    )
    if kind == "s3_repository":
        source = payload["request"]["source"]
        statement = statement.where(
            source["kind"].as_string() == kind,
            source["model"]["name"].as_string() == model_name,
        )
    else:
        # Legacy single-object receipts can omit the default kind.
        statement = statement.where(
            (payload["request"]["source"]["kind"].as_string() == "s3")
            | payload["request"]["source"]["kind"].as_string().is_(None)
        )
    if version is None:
        statement = statement.where(payload["request"]["source"]["version_id"].as_string().is_(None))
    else:
        statement = statement.where(payload["source"]["version_id"].as_string() == version)
    matches = session.exec(statement.order_by(col(HandoffEntity.created_at), col(HandoffEntity.id)))
    if model_name is None:
        return matches.first()
    # JSON array equality differs between PostgreSQL and SQLite. Filter by name
    # in SQL and compare the exact version set on the matching receipts.
    return next(
        (row for row in matches
         if tuple(sorted(row.payload["request"]["source"]["model"]["versions"])) == model_versions),
        None,
    )


def find_identity(session: Session, identity: str) -> HandoffEntity | None:
    return session.exec(select(HandoffEntity).where(HandoffEntity.identity_digest == identity)).first()


def for_tasks(session: Session, task_ids: list[UUID]) -> list[HandoffEntity]:
    if not task_ids:
        return []
    return list(session.exec(select(HandoffEntity).where(col(HandoffEntity.task_id).in_(task_ids))).all())


def get(session: Session, handoff_id: UUID) -> HandoffEntity:
    row = session.get(HandoffEntity, handoff_id)
    if row is None:
        raise NotFoundError("Handoff not found")
    return row


def add(session: Session, row: HandoffEntity, recipients: list[int]) -> None:
    session.add(row)
    session.flush()
    session.add_all([HandoffNotificationEntity(handoff_id=row.id, user_id=user) for user in recipients])
    session.flush()


def notifications(session: Session, actor: int, limit: int, offset: int) -> list[HandoffNotificationEntity]:
    return list(
        session.exec(
            select(HandoffNotificationEntity)
            .where(HandoffNotificationEntity.user_id == actor)
            .order_by(
                col(HandoffNotificationEntity.read_at).is_not(None),
                col(HandoffNotificationEntity.created_at).desc(),
                col(HandoffNotificationEntity.id),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )


def mark_read(session: Session, actor: int, notification_id: UUID) -> HandoffNotificationEntity:
    row = session.exec(
        select(HandoffNotificationEntity)
        .where(HandoffNotificationEntity.id == notification_id, HandoffNotificationEntity.user_id == actor)
        .with_for_update()
    ).first()
    if row is None:
        raise NotFoundError("Notification not found")
    if row.read_at is None:
        row.read_at = datetime.utcnow()
    session.add(row)
    session.commit()
    return row
