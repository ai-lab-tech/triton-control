"""Version-one canonical artifact identities, independent of database/services."""

import hashlib
import json
from typing import Any

from app.schemas.lineage import InventoryV1


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def inventory_digest(inventory: InventoryV1) -> str:
    data = inventory.model_dump(mode="json")
    data["files"] = sorted(data["files"], key=lambda item: item["path"].encode("utf-8"))
    return digest(data)
