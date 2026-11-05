"""Persistence for the singleton registry configuration."""

from sqlmodel import Session

from app.db.entities import RegistryConfigEntity


def get(session: Session) -> RegistryConfigEntity | None:
    return session.get(RegistryConfigEntity, 1)


def save(session: Session, encrypted_credentials: str) -> None:
    row = get(session) or RegistryConfigEntity()
    row.dockerconfigjson_enc = encrypted_credentials
    session.add(row)
    session.commit()
