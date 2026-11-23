from uuid import UUID

from sqlalchemy import select as select_columns
from sqlmodel import Session, col, select

from app.db.entities import PreparationTaskEventEntity

STARTED = "conversion_export_started"
INTERRUPTED = "conversion_export_interrupted"
VERIFIED = "conversion_export_verified"
EXPORT_ACTIONS = (STARTED, INTERRUPTED, VERIFIED)


def latest_event(
    session: Session, task_id: UUID, export_id: UUID, *, verified_only: bool = False
) -> PreparationTaskEventEntity | None:
    return session.exec(
        select(PreparationTaskEventEntity)
        .where(
            col(PreparationTaskEventEntity.task_id) == task_id,
            col(PreparationTaskEventEntity.action).in_((VERIFIED,) if verified_only else EXPORT_ACTIONS),
            col(PreparationTaskEventEntity.changes)["export_id"].as_string() == str(export_id),
        )
        .order_by(col(PreparationTaskEventEntity.revision).desc())
        .limit(1)
    ).first()


def statuses(session: Session, task_id: UUID) -> list[dict[str, str | None]]:
    """Project receipt headers without loading object inventories."""
    change = col(PreparationTaskEventEntity.changes)
    statement = (
        select_columns(
            col(PreparationTaskEventEntity.action),
            change["export_id"].as_string(),
            change["directory"].as_string(),
            change["bucket"].as_string(),
            change["prefix"].as_string(),
        )
        .where(
            col(PreparationTaskEventEntity.task_id) == task_id,
            col(PreparationTaskEventEntity.action).in_(EXPORT_ACTIONS),
        )
        .order_by(col(PreparationTaskEventEntity.revision).desc())
    )
    latest = {}
    for action, export_id, directory, bucket, prefix in session.execute(statement):
        if export_id not in latest:
            latest[export_id] = {
                "export_id": export_id,
                "directory": directory,
                "bucket": bucket,
                "prefix": prefix,
                "status": action.removeprefix("conversion_export_"),
            }
    return list(latest.values())
