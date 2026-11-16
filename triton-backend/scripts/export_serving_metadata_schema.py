"""Export/check deterministic shared schemas and identical installed-package copies."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.serving_metadata import DOCUMENT_MODELS  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    backend = Path(__file__).resolve().parents[1]
    roots = (backend.parent / "contracts/serving/v1alpha1", backend / "app/services/serving_contracts")
    stale = []
    expected = {f"{kind}.schema.json" for kind in DOCUMENT_MODELS}
    for kind, model in DOCUMENT_MODELS.items():
        schema = model.model_json_schema(by_alias=True)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"urn:triton-control:serving:v1alpha1:{kind}"
        content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        for root in roots:
            path = root / f"{kind}.schema.json"
            if args.check:
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    stale.append(str(path))
            else:
                root.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    for root in roots:
        stale.extend(str(path) for path in root.glob("*.schema.json") if path.name not in expected)
    if stale:
        print("Stale serving schemas:\n" + "\n".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
