"""Data-access helpers for workflow-scoped S3 credential records."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.db.entities import WorkflowS3CredentialEntity


def find_by_id(session: Session, credential_id: int) -> WorkflowS3CredentialEntity | None:
    return session.get(WorkflowS3CredentialEntity, credential_id)


def find_by_name(session: Session, name: str) -> WorkflowS3CredentialEntity | None:
    return session.exec(
        select(WorkflowS3CredentialEntity).where(WorkflowS3CredentialEntity.name == name),
    ).first()


def find_by_secret_name(session: Session, namespace: str, secret_name: str) -> WorkflowS3CredentialEntity | None:
    return session.exec(
        select(WorkflowS3CredentialEntity).where(
            WorkflowS3CredentialEntity.namespace == namespace,
            WorkflowS3CredentialEntity.secret_name == secret_name,
        ),
    ).first()


def list_all(session: Session) -> list[WorkflowS3CredentialEntity]:
    return list(
        session.exec(
            select(WorkflowS3CredentialEntity).order_by(WorkflowS3CredentialEntity.created_at.desc()),  # type: ignore[attr-defined]
        ).all(),
    )


def save(
    session: Session,
    credential: WorkflowS3CredentialEntity,
    *,
    refresh: bool = True,
) -> WorkflowS3CredentialEntity:
    session.add(credential)
    session.commit()
    if refresh:
        session.refresh(credential)
    return credential


def create(session: Session, **values: Any) -> WorkflowS3CredentialEntity:
    return save(session, WorkflowS3CredentialEntity(**values))


def delete(session: Session, credential: WorkflowS3CredentialEntity) -> None:
    session.delete(credential)
    session.commit()
