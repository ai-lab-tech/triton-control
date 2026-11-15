"""Insert-only capture receipts. Candidate publication serializes same-origin imports."""

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.db.entities import CandidateImportEntity, ServingCandidateEntity
from app.exceptions import ConflictError


def find(session: Session, installation: str, record: str) -> CandidateImportEntity | None:
    return session.get(CandidateImportEntity, (installation, record))


def lock_candidate(session: Session, installation: str, record: str) -> None:
    # #25 insert/savepoint handles the absent-row race; lock its resulting row to
    # serialize receipts even when the candidate was published by another caller.
    from sqlmodel import select

    session.exec(
        select(ServingCandidateEntity)
        .where(
            ServingCandidateEntity.installation_id == installation,
            ServingCandidateEntity.record_id == record,
        )
        .with_for_update()
    ).one()


def check(
    existing: CandidateImportEntity, candidate_digest: str, import_digest: str, owner_user_id: int, task_id: UUID | None
) -> None:
    if (existing.candidate_digest, existing.import_digest, existing.owner_user_id, existing.task_id) != (
        candidate_digest,
        import_digest,
        owner_user_id,
        task_id,
    ):
        raise ConflictError("Candidate origin already has a different immutable import")


def insert(
    session: Session,
    installation: str,
    record: str,
    candidate_digest: str,
    import_digest: str,
    owner_user_id: int,
    task_id: UUID | None,
    result: dict[str, Any],
) -> None:
    session.add(
        CandidateImportEntity(
            installation_id=installation,
            record_id=record,
            candidate_digest=candidate_digest,
            import_digest=import_digest,
            owner_user_id=owner_user_id,
            task_id=task_id,
            result=result,
        )
    )
    session.flush()
