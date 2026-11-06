"""Data-access helpers for reusable S3 deployment profiles."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.entities import S3ProfileEntity
from app.exceptions import ConflictError


def get(session: Session, profile_id: int) -> S3ProfileEntity | None:
    return session.get(S3ProfileEntity, profile_id)


def list_all(session: Session) -> list[S3ProfileEntity]:
    return list(session.exec(select(S3ProfileEntity).order_by(S3ProfileEntity.name)).all())


def list_by_name(session: Session, name: str) -> list[S3ProfileEntity]:
    return list(session.exec(select(S3ProfileEntity).where(S3ProfileEntity.name == name)).all())


def save(session: Session, profile: S3ProfileEntity, *, refresh: bool = True) -> S3ProfileEntity:
    name, profile_id = profile.name, profile.id
    session.add(profile)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if any(row.id != profile_id for row in list_by_name(session, name)):
            raise ConflictError("S3 profile name already exists") from None
        raise
    if refresh:
        session.refresh(profile)
    return profile


def create(session: Session, **values: Any) -> S3ProfileEntity:
    return save(session, S3ProfileEntity(**values))


def delete(session: Session, profile: S3ProfileEntity) -> None:
    session.delete(profile)
    session.commit()
