"""Build attempt persistence; caller holds the operation lease/row lock for writes."""

from datetime import datetime

from sqlalchemy import exists
from sqlmodel import Session, col, select

from app.db.entities import BuildAttemptEntity, BuildEvidenceReferenceEntity
from app.exceptions import ConflictError, NotFoundError


def attempts(session: Session, operation_id: str) -> list[BuildAttemptEntity]:
    return list(session.exec(select(BuildAttemptEntity).where(
        BuildAttemptEntity.operation_id == operation_id,
    ).order_by(col(BuildAttemptEntity.number))).all())


def latest(session: Session, operation_id: str) -> BuildAttemptEntity | None:
    return session.exec(select(BuildAttemptEntity).where(
        BuildAttemptEntity.operation_id == operation_id,
    ).order_by(col(BuildAttemptEntity.number).desc()).execution_options(populate_existing=True)).first()


def get(session: Session, operation_id: str, number: int, *, lock: bool = False) -> BuildAttemptEntity:
    query = select(BuildAttemptEntity).where(
        BuildAttemptEntity.operation_id == operation_id, BuildAttemptEntity.number == number,
    ).execution_options(populate_existing=True)
    row = session.exec(query.with_for_update() if lock else query).first()
    if row is None:
        raise NotFoundError("Build attempt not found")
    return row


def referenced(session: Session, row: BuildAttemptEntity) -> bool:
    return session.exec(select(BuildEvidenceReferenceEntity).where(
        BuildEvidenceReferenceEntity.operation_id == row.operation_id,
        BuildEvidenceReferenceEntity.attempt == row.number,
    )).first() is not None


def retain_for_release(session: Session, operation_id: str, number: int, release_digest: str) -> None:
    """Trusted release service hook, in the same transaction as release publication."""
    row = get(session, operation_id, number, lock=True)
    if row.state != "succeeded" or row.logs_expired:
        raise ConflictError("Complete retained build evidence is required")
    key = (operation_id, number, release_digest)
    if session.get(BuildEvidenceReferenceEntity, key) is None:
        session.add(BuildEvidenceReferenceEntity(operation_id=operation_id, attempt=number,
                                                  release_digest=release_digest))
        session.flush()


def expire_logs(session: Session, stamp: datetime, limit: int = 100) -> int:
    rows = session.exec(select(BuildAttemptEntity).where(
        col(BuildAttemptEntity.logs_expires_at) <= stamp, col(BuildAttemptEntity.logs_expired).is_(False),
        col(BuildAttemptEntity.cleaned).is_(True),
        ~exists().where(col(BuildEvidenceReferenceEntity.operation_id) == col(BuildAttemptEntity.operation_id),
                        col(BuildEvidenceReferenceEntity.attempt) == col(BuildAttemptEntity.number)),
    ).order_by(col(BuildAttemptEntity.logs_expires_at)).with_for_update(skip_locked=True).limit(limit)).all()
    count = 0
    for row in rows:
        if not referenced(session, row):
            row.logs, row.logs_expired = "", True
            session.add(row)
            count += 1
    session.commit()
    return count


def holds_context(session: Session, context_id: str) -> bool:
    return session.exec(select(BuildAttemptEntity).where(
        BuildAttemptEntity.context_id == context_id, col(BuildAttemptEntity.cleaned).is_(False),
    )).first() is not None
