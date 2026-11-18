"""Immutable experiment receipts; publication joins the caller's fenced transaction."""

from uuid import UUID

from sqlmodel import Session

from app.db.entities import PreparationSnapshotEntity
from app.exceptions import NotFoundError


def find(session: Session, snapshot_id: UUID) -> PreparationSnapshotEntity | None:
    return session.get(PreparationSnapshotEntity, snapshot_id)


def get(session: Session, snapshot_id: UUID) -> PreparationSnapshotEntity:
    row = find(session, snapshot_id)
    if row is None:
        raise NotFoundError("Preparation snapshot not found")
    return row


def add(session: Session, row: PreparationSnapshotEntity) -> None:
    session.add(row)
    session.flush()
