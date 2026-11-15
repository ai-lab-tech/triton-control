"""Export the shared version-one contracts deterministically."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.lineage import InventoryV1, ReleaseLineageV1, ServingCandidateV1  # noqa: E402

root = Path(__file__).resolve().parents[2] / "contracts" / "lineage" / "v1"
root.mkdir(parents=True, exist_ok=True)
for model in (InventoryV1, ServingCandidateV1, ReleaseLineageV1):
    (root / (model.__name__ + ".schema.json")).write_text(
        json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
