"""Transactional model reservations and run serialization."""

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import ModelPerfJobEntity, TritonInstanceEntity
from app.exceptions import ConflictError, NotFoundError

ACTIVE = ("creating", "pending", "running", "stopping")


def active(session: Session, instance_id: int, model_name: str) -> ModelPerfJobEntity | None:
    return session.exec(select(ModelPerfJobEntity).where(
        ModelPerfJobEntity.instance_id == instance_id, ModelPerfJobEntity.model_name == model_name,
        col(ModelPerfJobEntity.state).in_(ACTIVE),
    )).first()


def lock_instance(session: Session, instance_id: int) -> TritonInstanceEntity:
    row = session.exec(select(TritonInstanceEntity).where(
        TritonInstanceEntity.id == instance_id,
    ).with_for_update().execution_options(populate_existing=True)).first()
    if row is None:
        raise NotFoundError("Instance not found")
    return row


def reserve(session: Session, row: ModelPerfJobEntity) -> None:
    instance = lock_instance(session, row.instance_id)
    if instance.perf_deleting:
        raise ConflictError("Instance deletion is in progress")
    existing = active(session, row.instance_id, row.model_name)
    if existing:
        raise ConflictError(f"A performance run is already active for this model: {existing.id}")
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = active(session, row.instance_id, row.model_name)
        if existing:
            raise ConflictError(f"A performance run is already active for this model: {existing.id}") from None
        raise


def lock_run(session: Session, run_id: str, *, skip_locked: bool = False) -> ModelPerfJobEntity | None:
    return session.exec(select(ModelPerfJobEntity).where(ModelPerfJobEntity.id == run_id).with_for_update(
        skip_locked=skip_locked,
    ).execution_options(populate_existing=True)).first()


def latest(session: Session, instance_id: int, model_name: str, version: str) -> ModelPerfJobEntity | None:
    return session.exec(select(ModelPerfJobEntity).where(
        ModelPerfJobEntity.instance_id == instance_id, ModelPerfJobEntity.model_name == model_name,
        ModelPerfJobEntity.model_version == version,
    ).order_by(col(ModelPerfJobEntity.created_at).desc())).first()


def pending_ids(session: Session) -> list[str]:
    return list(session.exec(select(ModelPerfJobEntity.id).where(
        col(ModelPerfJobEntity.cleaned).is_(False),
    ).order_by(col(ModelPerfJobEntity.created_at))).all())


def for_instance(session: Session, instance_id: int) -> list[ModelPerfJobEntity]:
    return list(session.exec(select(ModelPerfJobEntity).where(ModelPerfJobEntity.instance_id == instance_id)).all())


def delete_for_instance(session: Session, instance_id: int) -> None:
    session.exec(delete(ModelPerfJobEntity).where(col(ModelPerfJobEntity.instance_id) == instance_id))
    session.commit()
