"""Persistence helpers for local-account lifecycle state."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.db.entities import (
    AccountLifecycleTokenEntity,
    EmailConfigEntity,
    RecoveryRateLimitEntity,
    SecurityEventEntity,
)


def create_token(session: Session, **values: Any) -> AccountLifecycleTokenEntity:
    entity = AccountLifecycleTokenEntity(**values)
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def find_token_by_hash(session: Session, token_hash: str) -> AccountLifecycleTokenEntity | None:
    return session.exec(
        select(AccountLifecycleTokenEntity).where(AccountLifecycleTokenEntity.token_hash == token_hash)
    ).first()


def list_active_tokens(
    session: Session, user_id: int, purpose: str | None = None
) -> list[AccountLifecycleTokenEntity]:
    statement = select(AccountLifecycleTokenEntity).where(
        AccountLifecycleTokenEntity.user_id == user_id,
        AccountLifecycleTokenEntity.consumed_at.is_(None),  # type: ignore[union-attr]
        AccountLifecycleTokenEntity.revoked_at.is_(None),  # type: ignore[union-attr]
    )
    if purpose:
        statement = statement.where(AccountLifecycleTokenEntity.purpose == purpose)
    return list(session.exec(statement).all())


def save_token(session: Session, entity: AccountLifecycleTokenEntity) -> AccountLifecycleTokenEntity:
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def get_email_config(session: Session) -> EmailConfigEntity:
    entity = session.get(EmailConfigEntity, 1)
    if entity is None:
        entity = EmailConfigEntity(id=1)
        session.add(entity)
        session.commit()
        session.refresh(entity)
    return entity


def save_email_config(session: Session, entity: EmailConfigEntity) -> EmailConfigEntity:
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def add_security_event(session: Session, **values: Any) -> SecurityEventEntity:
    entity = SecurityEventEntity(**values)
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def increment_rate_bucket(
    session: Session, bucket_key: str, window_started_at: datetime
) -> int:
    entity = session.exec(
        select(RecoveryRateLimitEntity).where(
            RecoveryRateLimitEntity.bucket_key == bucket_key,
            RecoveryRateLimitEntity.window_started_at == window_started_at,
        )
    ).first()
    if entity is None:
        entity = RecoveryRateLimitEntity(bucket_key=bucket_key, window_started_at=window_started_at)
    else:
        entity.request_count += 1
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity.request_count

