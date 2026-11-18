"""Export public materialization contracts; --check detects schema drift."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.schemas.materialization import (  # noqa: E402
    MaterializationPlan,
    MaterializationReceipt,
    Normalization,
    Qualification,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    directory = ROOT.parent / "contracts/materialization/v1"
    for model in (MaterializationPlan, MaterializationReceipt, Normalization, Qualification):
        path = directory / (model.__name__ + ".schema.json")
        content = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit("Materialization schema drift: " + path.name)
        else:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


if __name__ == "__main__":
    main()
