"""Batch reads for already authorized task IDs."""
from uuid import UUID

from sqlalchemy import select as select_columns
from sqlmodel import Session, col, select

from app.db.entities import (
    CandidateImportEntity,
    PreparationSnapshotEntity,
    PreparationTaskEventEntity,
    TeamEntity,
    UserEntity,
)


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

def conversion_statuses(session: Session, task_id: UUID) -> list[dict[str, str | None]]:
    """Project receipt headers without loading potentially large object inventories."""
    change = col(PreparationTaskEventEntity.changes)
    rows = session.execute(select_columns(
        col(PreparationTaskEventEntity.action), change["export_id"].as_string(), change["directory"].as_string(),
        change["bucket"].as_string(), change["prefix"].as_string(),
    ).where(
        col(PreparationTaskEventEntity.task_id) == task_id, col(PreparationTaskEventEntity.action).in_([
            "conversion_export_started", "conversion_export_interrupted", "conversion_export_verified",
        ]),
    ).order_by(col(PreparationTaskEventEntity.revision).desc()))
    latest = {}
    for action, export_id, directory, bucket, prefix in rows:
        if export_id not in latest:
            latest[export_id] = {"export_id": export_id, "directory": directory, "bucket": bucket, "prefix": prefix,
                                 "status": action.removeprefix("conversion_export_")}
    return list(latest.values())
