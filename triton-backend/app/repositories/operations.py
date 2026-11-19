"""Durable reservations and leased, fenced PostgreSQL execution."""

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import OperationEntity
from app.exceptions import ConflictError, NotFoundError

HANDOFF_CAPTURE_KIND = "handoff.capture"

TERMINAL = {"succeeded", "failed", "cancelled"}


def now(session: Session) -> datetime:
    return cast(datetime, session.execute(select(func.timezone("UTC", func.clock_timestamp()))).scalar_one())


def get(session: Session, operation_id: str, *, lock: bool = False) -> OperationEntity:
    query = select(OperationEntity).where(OperationEntity.id == operation_id).execution_options(populate_existing=True)
    row = session.exec(query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFoundError("Operation not found")
    return row


def reserve(session: Session, row: OperationEntity) -> OperationEntity:
    row.created_at = row.updated_at = row.available_at = now(session)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(select(OperationEntity).where(
            OperationEntity.actor_id == row.actor_id, OperationEntity.kind == row.kind,
            OperationEntity.target == row.target, OperationEntity.idempotency_key == row.idempotency_key,
        )).first()
        if existing is None:
            raise
        if existing.payload_hash != row.payload_hash:
            raise ConflictError("Idempotency-Key already used with different input") from None
        return existing
    session.refresh(row)
    return row


def claim(session: Session, worker: str, kinds: list[str], seconds: int) -> OperationEntity | None:
    stamp = now(session)
    row = session.exec(select(OperationEntity).where(
        col(OperationEntity.kind).in_(kinds), col(OperationEntity.state).notin_(TERMINAL),
        OperationEntity.available_at <= stamp,
        or_(col(OperationEntity.lease_until).is_(None), col(OperationEntity.lease_until) <= stamp),
    ).order_by(col(OperationEntity.available_at), col(OperationEntity.id)).with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)).first()
    if row is None:
        session.rollback()
        return None
    row.worker, row.lease_until = worker, stamp + timedelta(seconds=seconds)
    row.generation += 1
    row.attempts += 1
    row.state, row.updated_at = "running", stamp
    session.add(row)
    session.commit()
    session.refresh(row)
    session.expunge(row)
    return row


def owned(session: Session, operation_id: str, worker: str, generation: int) -> OperationEntity:
    row = get(session, operation_id, lock=True)
    if (row.worker != worker or row.generation != generation or row.state in TERMINAL
            or row.lease_until is None or row.lease_until <= now(session)):
        session.rollback()
        raise ConflictError("Operation lease lost")
    return row


def heartbeat(session: Session, operation_id: str, worker: str, generation: int, seconds: int) -> None:
    row = owned(session, operation_id, worker, generation)
    row.lease_until = now(session) + timedelta(seconds=seconds)
    session.add(row)
    session.commit()


def complete(
    session: Session, operation_id: str, worker: str, generation: int,
    state: str, result: dict[str, Any], message: str, delay: int,
) -> None:
    row = owned(session, operation_id, worker, generation)
    if row.cancel_requested and state != "cancelled":
        state = "pending"
    row.state, row.result, row.message = state, result, message
    row.updated_at = now(session)
    row.available_at = row.updated_at + timedelta(seconds=delay)
    row.worker, row.lease_until = None, None
    session.add(row)
    session.commit()


def cancel(session: Session, operation_id: str) -> OperationEntity:
    row = get(session, operation_id, lock=True)
    if row.state not in {"succeeded", "cancelled"}:
        row.state = "pending"
        row.cancel_requested = True
        row.updated_at = now(session)
        row.available_at = row.updated_at
        session.add(row)
    session.commit()
    session.refresh(row)
    return row


def retry(session: Session, operation_id: str) -> OperationEntity:
    row = get(session, operation_id, lock=True)
    if row.state != "failed":
        session.rollback()
        raise ConflictError("Only failed operations can be retried")
    row.state, row.message = "pending", ""
    row.updated_at = now(session)
    row.available_at = row.updated_at
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
