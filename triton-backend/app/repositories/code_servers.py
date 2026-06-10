"""Data-access helpers for per-user code-server workspaces."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.db.entities import CodeServerEntity


def find_by_id(session: Session, code_server_id: int) -> CodeServerEntity | None:
    return session.get(CodeServerEntity, code_server_id)


def find_by_owner_and_name(session: Session, owner_user_id: int, name: str) -> CodeServerEntity | None:
    return session.exec(
        select(CodeServerEntity).where(
            CodeServerEntity.owner_user_id == owner_user_id,
            CodeServerEntity.name == name,
        ),
    ).first()


def list_for_owner(session: Session, owner_user_id: int) -> list[CodeServerEntity]:
    return list(
        session.exec(
            select(CodeServerEntity)
            .where(CodeServerEntity.owner_user_id == owner_user_id)
            .order_by(CodeServerEntity.created_at.desc()),  # type: ignore[attr-defined]
        ).all(),
    )


def save(session: Session, row: CodeServerEntity, *, refresh: bool = True) -> CodeServerEntity:
    session.add(row)
    session.commit()
    if refresh:
        session.refresh(row)
    return row


def create(session: Session, **values: Any) -> CodeServerEntity:
    return save(session, CodeServerEntity(**values))


def delete(session: Session, row: CodeServerEntity) -> None:
    session.delete(row)
    session.commit()
