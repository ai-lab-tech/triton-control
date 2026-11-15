"""Insert-only candidate publication, with race-safe idempotency."""

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.db.entities import ServingCandidateEntity
from app.exceptions import ConflictError


def find(session: Session, installation: str, record: str) -> ServingCandidateEntity | None:
    return session.get(ServingCandidateEntity, (installation, record))


def insert(session: Session, installation: str, record: str, digest: str, payload: str, inventories: str) -> None:
    existing = find(session, installation, record)
    if existing is None:
        try:
            with session.begin_nested():
                session.add(
                    ServingCandidateEntity(
                        installation_id=installation,
                        record_id=record,
                        digest=digest,
                        payload=payload,
                        inventories=inventories,
                    )
                )
                session.flush()
            return
        except IntegrityError:
            existing = find(session, installation, record)
            if existing is None:
                raise
    if existing.digest != digest or existing.payload != payload:
        raise ConflictError("Candidate identity already contains different immutable content")
