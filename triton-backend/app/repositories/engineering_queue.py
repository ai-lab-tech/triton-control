"""Batch reads for already authorized task IDs."""
from uuid import UUID

from sqlmodel import Session, col, select

from app.db.entities import CandidateImportEntity, PreparationSnapshotEntity, TeamEntity, UserEntity


def snapshots(session: Session, ids: list[UUID]) -> dict[UUID, PreparationSnapshotEntity]:
    return {row.id: row for row in session.exec(select(PreparationSnapshotEntity).where(
        col(PreparationSnapshotEntity.id).in_(ids),
    )).all()} if ids else {}


def names(session: Session, user_ids: set[int], team_ids: set[UUID]) -> tuple[dict[int, str], dict[UUID, str]]:
    users = {row.id: row.name for row in session.exec(select(UserEntity).where(
        col(UserEntity.id).in_(user_ids),
    )).all() if row.id is not None}
    teams = {row.id: row.name for row in session.exec(select(TeamEntity).where(
        col(TeamEntity.id).in_(team_ids),
    )).all()}
    return users, teams


def candidates(session: Session, task_id: UUID) -> list[CandidateImportEntity]:
    return list(session.exec(select(CandidateImportEntity).where(
        CandidateImportEntity.task_id == task_id,
    ).order_by(col(CandidateImportEntity.created_at), col(CandidateImportEntity.record_id))).all())
