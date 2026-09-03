"""Data-access helpers for ``UserEntity`` rows.

Provides fine-grained query and mutation functions used by service modules:
  ``count(session)``                      — total number of users (used to
                                             detect whether bootstrap is needed).
  ``find_by_id`` / ``find_by_email`` /
  ``find_by_oidc_subject``                — single-row lookups.
  ``list_all(session)``                   — all users ordered by creation time.
  ``has_any_admin(session)``              — ``True`` when at least one admin
                                             account exists.
  ``has_active_local_admin_login(session)``— ``True`` when at least one active
                                             admin with a password hash exists.
  ``create`` / ``save`` / ``delete``      — lifecycle helpers.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.db.entities import AccountLifecycleTokenEntity, UserEntity


def count(session: Session) -> int:
    return len(session.exec(select(UserEntity.id)).all())


def find_by_id(session: Session, user_id: int) -> UserEntity | None:
    return session.get(UserEntity, user_id)


def find_by_email(session: Session, email: str) -> UserEntity | None:
    return session.exec(select(UserEntity).where(UserEntity.email == email)).first()


def find_by_oidc_subject(session: Session, subject: str) -> UserEntity | None:
    return session.exec(select(UserEntity).where(UserEntity.oidc_subject == subject)).first()


def list_all(session: Session) -> list[UserEntity]:
    return list(session.exec(select(UserEntity).order_by(UserEntity.created_at.desc())).all())  # type: ignore[attr-defined]


def has_any_admin(session: Session) -> bool:
    rows = session.exec(select(UserEntity.role)).all()
    return any((role or "").strip().lower() == "admin" for role in rows)


def has_active_local_admin_login(session: Session) -> bool:
    users = session.exec(select(UserEntity)).all()
    for user in users:
        role = (user.role or "").strip().lower()
        if (
            (user.auth_provider or "").strip().lower() == "local"
            and bool(user.password_hash)
            and bool(user.is_active)
            and role == "admin"
        ):
            return True
    return False


def save(session: Session, user: UserEntity, *, refresh: bool = True) -> UserEntity:
    session.add(user)
    session.commit()
    if refresh:
        session.refresh(user)
    return user


def delete(session: Session, user: UserEntity) -> None:
    user_id = user.id
    if user_id is not None:
        # Tokens issued for this account become meaningless once the account is
        # removed and otherwise prevent deletion through their foreign key.
        session.exec(sa_delete(AccountLifecycleTokenEntity).where(col(AccountLifecycleTokenEntity.user_id) == user_id))
        # Keep tokens issued by this administrator valid, but detach their
        # optional audit reference before removing the administrator.
        session.exec(
            update(AccountLifecycleTokenEntity)
            .where(col(AccountLifecycleTokenEntity.created_by_user_id) == user_id)
            .values(created_by_user_id=None)
        )
    session.delete(user)
    session.commit()


def create(session: Session, **values: Any) -> UserEntity:
    return save(session, UserEntity(**values))
