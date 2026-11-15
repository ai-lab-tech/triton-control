"""Append-only qualification evidence; no credentials are persisted here."""

from typing import Any

from sqlmodel import Session, col, select

from app.db.entities import BuildQualificationEntity


def append(session: Session, **values: Any) -> BuildQualificationEntity:
    record = BuildQualificationEntity(**values)
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def latest(session: Session, policy_hash: str, fixture_revision: str) -> BuildQualificationEntity | None:
    return session.exec(
        select(BuildQualificationEntity)
        .where(
            BuildQualificationEntity.policy_hash == policy_hash,
            BuildQualificationEntity.fixture_revision == fixture_revision,
        )
        .order_by(col(BuildQualificationEntity.created_at).desc(), col(BuildQualificationEntity.id).desc())
    ).first()
