"""Canonical identity and authorized immutable publication."""

from typing import Any

from sqlmodel import Session

from app.core.access_control import require_admin
from app.core.canonical import canonical_bytes as canonical_bytes
from app.core.canonical import digest as digest
from app.core.canonical import inventory_digest as inventory_digest
from app.exceptions import BadRequestError, NotFoundError
from app.repositories import lineage
from app.schemas.lineage import InventoryV1, ServingCandidateV1


def publish(
    session: Session, claims: dict[str, Any], candidate: ServingCandidateV1, inventories: tuple[InventoryV1, ...]
) -> str:
    # Initial provisioning API is admin-only; downstream task services add scoped grants.
    require_admin(claims)
    validate_publication(session, candidate, inventories)
    payload = candidate.model_dump(mode="json")
    identity = digest(payload)
    lineage.insert(
        session,
        candidate.origin.installation_id,
        candidate.origin.record_id,
        identity,
        canonical_bytes(payload).decode(),
        canonical_bytes([item.model_dump(mode="json") for item in inventories]).decode(),
    )
    return identity


def validate_publication(session: Session, candidate: ServingCandidateV1, inventories: tuple[InventoryV1, ...]) -> None:
    """Contextual contract checks; callers must enforce their own authorization."""
    available = {inventory_digest(item) for item in inventories}
    needed = {candidate.derived_from_snapshot.inventory, candidate.output_inventory}
    for attempt in candidate.attempts:
        needed.update((attempt.input_inventory, attempt.output_inventory))
    if not needed <= available:
        raise BadRequestError("Every referenced inventory must be supplied")
    previous = candidate.previous_serving_candidate
    if previous and lineage.find(session, previous.installation_id, previous.record_id) is None:
        raise NotFoundError("Previous serving candidate is not registered")
