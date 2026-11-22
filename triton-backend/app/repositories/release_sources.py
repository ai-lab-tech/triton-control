"""Transactional persistence for release source registrations and history."""

from sqlalchemy import text
from sqlmodel import Session, col, select

from app.db.entities import ReleaseSourceEntity, ReleaseSourceEventEntity
from app.schemas.release_sources import Provenance
from app.schemas.serving_metadata import MetadataSource

# All config and API writers take the same lock, even for an empty registry.
REGISTRY_LOCK_ID = 741293817


def lock_registry(session: Session) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": REGISTRY_LOCK_ID})


def registrations(session: Session) -> dict[str, ReleaseSourceEntity]:
    query = select(ReleaseSourceEntity).order_by(col(ReleaseSourceEntity.name))
    return {row.name: row for row in session.exec(query)}


def get(session: Session, name: str) -> ReleaseSourceEntity | None:
    return session.get(ReleaseSourceEntity, name)


def record(
    session: Session,
    previous: ReleaseSourceEntity | None,
    source: MetadataSource,
    provenance: Provenance,
    *,
    actor_id: int | None = None,
    retired: bool = False,
) -> ReleaseSourceEntity:
    document = source.model_dump(mode="json", by_alias=True)
    row = previous or ReleaseSourceEntity(name=source.metadata.name, revision=0)
    row.revision += 1
    row.provenance = provenance
    row.document = document
    row.retired = retired
    session.add(row)
    # Ensure the registration exists before inserting its audit snapshot.
    session.flush()
    session.add(ReleaseSourceEventEntity(
        source_name=row.name,
        revision=row.revision,
        provenance=provenance,
        retired=retired,
        actor_id=actor_id,
        document=document,
    ))
    return row
