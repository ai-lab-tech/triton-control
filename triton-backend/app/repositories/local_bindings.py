"""Persistence only; policy updates use compare-and-swap revisions."""

from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col

from app.db.entities import LocalBindingPolicyEntity, LocalCredentialEntity
from app.exceptions import ConflictError


def policy(session: Session) -> LocalBindingPolicyEntity | None:
    return session.get(LocalBindingPolicyEntity, 1, populate_existing=True)


def save_policy(session: Session, expected: int, payload: dict[str, Any]) -> int:
    try:
        if expected == 0:
            session.add(LocalBindingPolicyEntity(payload=payload))
            session.flush()
        else:
            result = session.execute(
                update(LocalBindingPolicyEntity)
                .where(
                    col(LocalBindingPolicyEntity.id) == 1,
                    col(LocalBindingPolicyEntity.revision) == expected,
                )
                .values(payload=payload, revision=expected + 1)
            )
            if cast(CursorResult[Any], result).rowcount != 1:
                session.rollback()
                raise ConflictError("Local policy changed; reload before saving")
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Local policy changed; reload before saving") from None
    return expected + 1


def credential(session: Session, name: str) -> LocalCredentialEntity | None:
    return session.get(LocalCredentialEntity, name, populate_existing=True)


def save_credential(session: Session, name: str, provider: str, scope: dict[str, Any], encrypted: str) -> None:
    row = credential(session, name)
    if row is None:
        row = LocalCredentialEntity(name=name, provider=provider, scope=scope, secret_enc=encrypted)
    else:
        row.provider, row.scope, row.secret_enc = provider, scope, encrypted
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Credential changed concurrently; retry") from None


def delete_credential(session: Session, name: str) -> None:
    row = credential(session, name)
    if row:
        session.delete(row)
        session.commit()
