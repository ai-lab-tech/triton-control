"""Export shared, versioned neutral importer contracts deterministically."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.candidate_import import ImportedCandidate, PreparedImportRequest  # noqa: E402

root = Path(__file__).resolve().parents[2] / "contracts" / "candidate-import" / "v1"
root.mkdir(parents=True, exist_ok=True)
for model in (PreparedImportRequest, ImportedCandidate):
    (root / (model.__name__ + ".schema.json")).write_text(
        json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
