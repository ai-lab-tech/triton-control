"""Data-access helpers for reusable S3 deployment profiles."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, col, or_, select

from app.db.entities import S3ProfileEntity, UserEntity


def get(session: Session, profile_id: int) -> S3ProfileEntity | None:
    return session.get(S3ProfileEntity, profile_id)


def list_for_owner(session: Session, owner_user_id: int) -> list[S3ProfileEntity]:
    query = (
        select(S3ProfileEntity)
        .where(
            or_(
                S3ProfileEntity.owner_user_id == owner_user_id,
                col(S3ProfileEntity.owner_user_id).in_(select(UserEntity.id).where(UserEntity.role == "admin")),
            )
        )
        .order_by(S3ProfileEntity.name)
    )
    return list(session.exec(query).all())


def find_for_owner(session: Session, owner_user_id: int, profile_id: int) -> S3ProfileEntity | None:
    return session.exec(
        select(S3ProfileEntity).where(
            or_(
                S3ProfileEntity.owner_user_id == owner_user_id,
                col(S3ProfileEntity.owner_user_id).in_(select(UserEntity.id).where(UserEntity.role == "admin")),
            ),
            S3ProfileEntity.id == profile_id,
        )
    ).first()


def find_by_name_for_owner(session: Session, owner_user_id: int, name: str) -> S3ProfileEntity | None:
    return session.exec(
        select(S3ProfileEntity).where(
            S3ProfileEntity.owner_user_id == owner_user_id,
            S3ProfileEntity.name == name,
        )
    ).first()


def save(session: Session, profile: S3ProfileEntity, *, refresh: bool = True) -> S3ProfileEntity:
    session.add(profile)
    session.commit()
    if refresh:
        session.refresh(profile)
    return profile


def create(session: Session, **values: Any) -> S3ProfileEntity:
    return save(session, S3ProfileEntity(**values))


def delete(session: Session, profile: S3ProfileEntity) -> None:
    session.delete(profile)
    session.commit()


def list_all(session: Session) -> list[S3ProfileEntity]:
    """Allow administrators to maintain legacy personal profiles as well."""
    return list(session.exec(select(S3ProfileEntity).order_by(S3ProfileEntity.name)).all())
