"""Transaction primitives for atomic deployment admission and execution."""

import hashlib
from datetime import timedelta

from sqlalchemy import text
from sqlmodel import Session, select

from app.db.entities import DeploymentIntentEntity, OperationEntity
from app.repositories import operations

KIND = "deployment.create.v1"


def lock_target(session: Session, target: str) -> None:
    if session.get_bind().dialect.name == "postgresql":
        key = int.from_bytes(hashlib.sha256((KIND + target).encode()).digest()[:8], signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def replay(session: Session, actor: int, target: str, key: str) -> OperationEntity | None:
    return session.exec(select(OperationEntity).where(
        OperationEntity.actor_id == actor, OperationEntity.kind == KIND,
        OperationEntity.target == target, OperationEntity.idempotency_key == key,
    )).first()


def get(session: Session, operation_id: str) -> DeploymentIntentEntity | None:
    return session.get(DeploymentIntentEntity, operation_id)


def for_instance(session: Session, instance_id: int) -> DeploymentIntentEntity | None:
    return session.exec(select(DeploymentIntentEntity).where(DeploymentIntentEntity.instance_id == instance_id)).first()


def retire(session: Session, intent: DeploymentIntentEntity) -> bool:
    row = operations.get(session, intent.operation_id, lock=True)
    session.refresh(intent)
    if intent.cleaned:
        return True
    intent.delete_requested = True
    row.cancel_requested = True
    row.state = "pending"
    row.available_at = operations.now(session)
    session.add_all([intent, row])
    session.commit()
    return False


def renew_deadline(session: Session, operation_id: str, seconds: int) -> None:
    intent = get(session, operation_id)
    if intent:
        intent.deadline_at = operations.now(session) + timedelta(seconds=seconds)
        session.add(intent)
