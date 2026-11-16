"""Build context rows; locks fence competing uploads and final publication."""

from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import BuildContextEntity, BuildContextReceiptEntity
from app.exceptions import NotFoundError


def get(session: Session, context_id: str, *, lock: bool = False) -> BuildContextEntity:
    query = (select(BuildContextEntity).where(BuildContextEntity.id == context_id)
             .execution_options(populate_existing=True))
    row = session.exec(query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFoundError("Build context not found")
    return row


def ensure(session: Session, context_id: str, **values: Any) -> BuildContextEntity:
    row = session.get(BuildContextEntity, context_id)
    if row is None:
        session.add(BuildContextEntity(id=context_id, **values))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
    return get(session, context_id)


def receipt(session: Session, context_id: str) -> BuildContextReceiptEntity | None:
    return session.get(BuildContextReceiptEntity, context_id)


def publish(session: Session, context_id: str, payload: dict[str, Any]) -> None:
    if receipt(session, context_id) is None:
        session.add(BuildContextReceiptEntity(id=context_id, payload=payload))
    session.commit()


def due(session: Session, stamp: datetime, limit: int = 100) -> list[str]:
    return list(session.exec(select(BuildContextEntity.id).where(
        BuildContextEntity.expires_at <= stamp, col(BuildContextEntity.expired).is_(False),
    ).order_by(col(BuildContextEntity.expires_at)).limit(limit)).all())
