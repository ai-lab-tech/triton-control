"""Storage for the installation build policy override."""

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.entities import BuildConfigEntity, BuildDestinationEntity
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
    destination = get_destination(session)
    if destination:
        session.delete(destination)
    commit(session)


def get_destination(session: Session) -> BuildDestinationEntity | None:
    return session.get(BuildDestinationEntity, 1, populate_existing=True)


def save_destination(session: Session, namespace: str, repositories: tuple[str, ...],
                     push_secret: str | None, encrypted_config: str | None, reuse_pull_credentials: bool) -> None:
    row = get_destination(session) or BuildDestinationEntity()
    row.reuse_pull_credentials = reuse_pull_credentials
    if encrypted_config is not None or push_secret or reuse_pull_credentials:
        row.push_config_enc = encrypted_config or None
        row.push_secret = push_secret
    row.namespace = namespace
    row.repositories = list(repositories)
    session.add(row)
    commit(session)


def commit(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Build settings changed concurrently. Reload and try again.") from None
