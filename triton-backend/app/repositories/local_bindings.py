"""Persistence only; policy updates use compare-and-swap revisions."""

from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import LocalBindingPolicyEntity, LocalCredentialEntity
from app.exceptions import ConflictError, ForbiddenError


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


def credential(session: Session, name: str, *, lock: bool = False) -> LocalCredentialEntity | None:
    query = (select(LocalCredentialEntity).where(LocalCredentialEntity.name == name)
             .execution_options(populate_existing=True))
    return session.exec(query.with_for_update() if lock else query).first()


def save_credential(session: Session, name: str, provider: str, scope: dict[str, Any], encrypted: str,
                    *, require_existing: bool = False) -> None:
    row = credential(session, name, lock=True)
    if row is None:
        if require_existing:
            raise ForbiddenError("New credential scopes require independent review")
        row = LocalCredentialEntity(name=name, provider=provider, scope=scope, secret_enc=encrypted)
    else:
        if row.provider != provider or row.scope != scope:
            session.rollback()
            raise ForbiddenError("Delete the credential before changing its scope or purpose")
        row.secret_enc = encrypted
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
