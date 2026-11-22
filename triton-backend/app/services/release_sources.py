"""Manage source registration independently of provider access and serving execution."""

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter
from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.db.entities import ReleaseSourceEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.repositories import release_sources as repository
from app.schemas.release_sources import Provenance, ReleaseSource
from app.schemas.serving_metadata import MetadataSource
from app.services.serving_metadata import MAX_BYTES, parse_document

CONFIG_JSON = "TRITON_RELEASE_SOURCES_JSON"
CONFIG_FILE = "TRITON_RELEASE_SOURCES_FILE"
ConfigProvenance = Literal["environment", "file"]
_SOURCE_LIST: TypeAdapter[list[MetadataSource]] = TypeAdapter(list[MetadataSource])
_PROVENANCE: TypeAdapter[Provenance] = TypeAdapter(Provenance)


def _admin(session: Session, claims: dict[str, Any]) -> int:
    if claims.get("token_use"):
        raise ForbiddenError("Interactive administrator credentials required")
    user = require_user_entity(session, claims)
    if user.id is None or not user.is_active or user.role.strip().lower() != "admin":
        raise ForbiddenError("Release source administration requires an active administrator")
    return user.id


def _read_configuration() -> tuple[ConfigProvenance, dict[str, MetadataSource]]:
    inline = os.environ.get(CONFIG_JSON)
    path = os.environ.get(CONFIG_FILE)
    if inline is not None and path is not None:
        raise BadRequestError(f"Set only one of {CONFIG_JSON} and {CONFIG_FILE}")
    try:
        if path is not None:
            with Path(path).open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
        else:
            raw = (inline if inline is not None else "[]").encode("utf-8")
        sources = _SOURCE_LIST.validate_python(parse_document(raw, "json"))
    except (OSError, ValueError):
        setting = CONFIG_FILE if path is not None else CONFIG_JSON
        raise BadRequestError(f"{setting} must contain a valid JSON array of MetadataSource documents") from None

    by_name: dict[str, MetadataSource] = {}
    for source in sources:
        name = source.metadata.name
        if name in by_name:
            raise ConflictError(f"Duplicate configured source ID: {name}")
        by_name[name] = source
    return ("file" if path is not None else "environment"), by_name


def _source(row: ReleaseSourceEntity) -> MetadataSource:
    return MetadataSource.model_validate(row.document)


def _active(rows: dict[str, ReleaseSourceEntity]) -> dict[str, MetadataSource]:
    return {name: _source(row) for name, row in rows.items() if not row.retired}


def _validate_target_ownership(sources: dict[str, MetadataSource]) -> None:
    owners: dict[str, str] = {}
    for name, source in sources.items():
        for target in source.spec.allowed_targets:
            owner = owners.setdefault(target, name)
            if owner != name:
                raise ConflictError(f"Target {target} is already claimed by source {owner}")


def _require_changes() -> None:
    if installation_policy.mode() == "production":
        installation_policy.require("production_changes")


def sync_configuration(session: Session) -> None:
    """Apply one complete startup snapshot or leave the registry intact on failure."""
    provenance, configured = _read_configuration()
    repository.lock_registry(session)
    rows = repository.registrations(session)
    database = {name: row for name, row in rows.items() if row.provenance == "database"}
    for name in configured:
        if name in database:
            raise ConflictError(f"Configured source ID collides with a database registration: {name}")
    _validate_target_ownership(_active(database) | configured)

    updates = {
        name: source for name, source in configured.items()
        if name not in rows or rows[name].retired or rows[name].provenance != provenance
        or _source(rows[name]) != source
    }
    removals = [
        row for row in rows.values()
        if row.provenance != "database" and not row.retired and row.name not in configured
    ]
    if updates or removals:
        _require_changes()
    for name, source in updates.items():
        repository.record(session, rows.get(name), source, provenance)
    for row in removals:
        repository.record(session, row, _source(row), _PROVENANCE.validate_python(row.provenance), retired=True)
    session.commit()


def _response(row: ReleaseSourceEntity) -> ReleaseSource:
    return ReleaseSource(
        source=_source(row),
        provenance=_PROVENANCE.validate_python(row.provenance),
        revision=row.revision,
        editable=row.provenance == "database" and installation_policy.mode() == "development",
    )


def list_sources(session: Session, claims: dict[str, Any]) -> list[ReleaseSource]:
    _admin(session, claims)
    return [_response(row) for row in repository.registrations(session).values() if not row.retired]


def get_source(session: Session, claims: dict[str, Any], name: str) -> ReleaseSource:
    _admin(session, claims)
    return _response(_registered(repository.get(session, name)))


def _registered(row: ReleaseSourceEntity | None) -> ReleaseSourceEntity:
    if row is None or row.retired:
        raise NotFoundError("Release source not found")
    return row


def save_source(
    session: Session,
    claims: dict[str, Any],
    name: str,
    source: MetadataSource,
    *,
    expected_revision: int | None = None,
) -> ReleaseSource:
    actor_id = _admin(session, claims)
    _require_changes()
    if source.metadata.name != name:
        raise BadRequestError("The source ID must match the URL and cannot be changed")

    repository.lock_registry(session)
    rows = repository.registrations(session)
    previous = rows.get(name)
    if previous is not None and previous.provenance != "database":
        raise ConflictError("Source ID is reserved by deployment configuration")
    if expected_revision is None:
        if previous is not None and not previous.retired:
            raise ConflictError("Source ID is already registered")
    else:
        previous = _registered(previous)
        if previous.revision != expected_revision:
            raise ConflictError("Source changed concurrently; reload before saving")
        if _source(previous) == source:
            response = _response(previous)
            session.commit()
            return response

    _validate_target_ownership(_active(rows) | {name: source})
    response = _response(repository.record(session, previous, source, "database", actor_id=actor_id))
    session.commit()
    return response


def delete_source(session: Session, claims: dict[str, Any], name: str, expected_revision: int) -> None:
    actor_id = _admin(session, claims)
    _require_changes()
    repository.lock_registry(session)
    row = _registered(repository.registrations(session).get(name))
    if row.provenance != "database":
        raise ConflictError("Configuration-owned sources are read-only")
    if row.revision != expected_revision:
        raise ConflictError("Source changed concurrently; reload before removing")
    repository.record(session, row, _source(row), "database", actor_id=actor_id, retired=True)
    session.commit()
