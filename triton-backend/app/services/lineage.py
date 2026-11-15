"""Canonical identity and authorized immutable publication."""

import hashlib
import json
from typing import Any

from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import BadRequestError, NotFoundError
from app.repositories import lineage
from app.schemas.lineage import InventoryV1, ServingCandidateV1


def canonical_bytes(value: Any) -> bytes:
    """V1 uses UTF-8, sorted keys, compact JSON; contract contains no floats."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def inventory_digest(inventory: InventoryV1) -> str:
    data = inventory.model_dump(mode="json")
    data["files"] = sorted(data["files"], key=lambda item: item["path"].encode("utf-8"))
    return digest(data)


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
