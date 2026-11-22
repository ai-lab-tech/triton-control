"""Storage for the installation build policy override."""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.entities import BuildConfigEntity
from app.exceptions import ConflictError


def get(session: Session) -> BuildConfigEntity | None:
    return session.get(BuildConfigEntity, 1, populate_existing=True)


def save(session: Session, values: dict[str, Any] | None) -> None:
    row = get(session)
    if values is None:
        if row:
            session.delete(row)
    else:
        row = row or BuildConfigEntity()
        row.policy = values
        session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Build settings changed concurrently. Reload and try again.") from None
