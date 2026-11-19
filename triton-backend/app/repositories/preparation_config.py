"""Persistence of admin overrides; no policy or environment lookup."""

from typing import Any

from sqlmodel import Session

from app.db.entities import PreparationConfigEntity


def get(session: Session) -> PreparationConfigEntity | None:
    return session.get(PreparationConfigEntity, 1)


def save(session: Session, limits: dict[str, Any] | None) -> None:
    row = get(session)
    if limits is None:
        if row is not None:
            session.delete(row)
    else:
        row = row or PreparationConfigEntity()
        row.limits = limits
        session.add(row)
    session.commit()
