"""Handoff receipt and recipient-scoped notification persistence."""

from datetime import datetime
from uuid import UUID

from sqlmodel import Session, col, select

from app.db.entities import HandoffEntity, HandoffNotificationEntity
from app.exceptions import NotFoundError


def find(session: Session, identity: str) -> HandoffEntity | None:
    return session.exec(select(HandoffEntity).where(HandoffEntity.identity_digest == identity)).first()


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
