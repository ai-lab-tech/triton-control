"""Preparation persistence. Mutations and their events share one transaction."""

from typing import Any
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import (
    PreparationAttemptEntity,
    PreparationTaskEntity,
    PreparationTaskEventEntity,
    TeamEntity,
    UserEntity,
    UserTeamEntity,
)
from app.exceptions import ConflictError, NotFoundError


def get(session: Session, task_id: UUID, *, lock: bool = False) -> PreparationTaskEntity:
    statement = select(PreparationTaskEntity).where(PreparationTaskEntity.id == task_id)
    if lock:
        statement = statement.with_for_update()
    row = session.exec(statement.execution_options(populate_existing=True)).first()
    if row is None:
        raise NotFoundError("Preparation task not found")
    return row


def current_user(session: Session, user_id: int) -> UserEntity | None:
    return session.exec(
        select(UserEntity).where(UserEntity.id == user_id).execution_options(populate_existing=True)
    ).first()


def queue(
    session: Session,
    actor_id: int,
    team_ids: list[UUID],
    *,
    admin: bool,
    limit: int,
    offset: int,
) -> list[PreparationTaskEntity]:
    statement = select(PreparationTaskEntity)
    if not admin:
        statement = statement.where(
            (col(PreparationTaskEntity.submitter_user_id) == actor_id)
            | col(PreparationTaskEntity.assigned_team_id).in_(team_ids)
        )
    return list(
        session.exec(
            statement.order_by(col(PreparationTaskEntity.created_at), col(PreparationTaskEntity.id))
            .offset(offset)
            .limit(limit)
            .execution_options(populate_existing=True)
        ).all()
    )


def add_task(session: Session, **values: Any) -> PreparationTaskEntity:
    task = PreparationTaskEntity(**values)
    session.add(task)
    session.flush()
    return task


def add_event(
    session: Session, task: PreparationTaskEntity, actor_id: int, action: str, changes: dict[str, Any]
) -> None:
    session.add(
        PreparationTaskEventEntity(
            task_id=task.id, revision=task.revision, actor_user_id=actor_id, action=action, changes=changes
        )
    )


def events(session: Session, task_id: UUID, limit: int, offset: int) -> list[PreparationTaskEventEntity]:
    return list(
        session.exec(
            select(PreparationTaskEventEntity)
            .where(PreparationTaskEventEntity.task_id == task_id)
            .order_by(col(PreparationTaskEventEntity.revision).desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )


def add_attempt(session: Session, task_id: UUID, actor_id: int, attempt: dict[str, Any]) -> None:
    existing = session.exec(
        select(PreparationAttemptEntity.id).where(
            PreparationAttemptEntity.task_id == task_id,
            PreparationAttemptEntity.installation_id == attempt["origin"]["installation_id"],
            PreparationAttemptEntity.record_id == attempt["origin"]["record_id"],
        )
    ).first()
    if existing is not None:
        raise ConflictError("Engineering attempt already recorded")
    session.add(
        PreparationAttemptEntity(
            task_id=task_id,
            actor_user_id=actor_id,
            installation_id=attempt["origin"]["installation_id"],
            record_id=attempt["origin"]["record_id"],
            attempt=attempt,
        )
    )


def attempts(session: Session, task_id: UUID, limit: int, offset: int) -> list[PreparationAttemptEntity]:
    return list(
        session.exec(
            select(PreparationAttemptEntity)
            .where(PreparationAttemptEntity.task_id == task_id)
            .order_by(col(PreparationAttemptEntity.created_at), col(PreparationAttemptEntity.id))
            .offset(offset)
            .limit(limit)
        ).all()
    )


def persist(session: Session, *, commit: bool = True) -> None:
    try:
        if commit:
            session.commit()
        else:
            session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Preparation revision or attempt already exists") from exc


def eligible_members(
    session: Session, team_ids: set[UUID] | None = None, *, actor_id: int | None = None
) -> dict[UUID, set[int]]:
    """Read eligibility in one query, scoped either to an actor or a queue page."""
    if team_ids is not None and not team_ids:
        return {}
    statement = (
        select(UserTeamEntity.team_id, UserTeamEntity.user_id)
        .join(TeamEntity, col(TeamEntity.id) == col(UserTeamEntity.team_id))
        .join(UserEntity, col(UserEntity.id) == col(UserTeamEntity.user_id))
        .where(
            col(TeamEntity.is_active).is_(True),
            col(UserTeamEntity.is_active).is_(True),
            col(UserEntity.is_active).is_(True),
            func.lower(func.trim(col(UserEntity.role))).in_(["member", "admin"]),
        )
    )
    if team_ids is not None:
        statement = statement.where(col(UserTeamEntity.team_id).in_(team_ids))
    if actor_id is not None:
        statement = statement.where(UserTeamEntity.user_id == actor_id)
    result: dict[UUID, set[int]] = {}
    for team_id, user_id in session.exec(statement):
        result.setdefault(team_id, set()).add(user_id)
    return result
