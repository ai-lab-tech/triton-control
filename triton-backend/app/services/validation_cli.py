"""Offline validation contract validator, usable from an installed wheel."""

import argparse
import json
import sys
from pathlib import Path

from app.services.serving_metadata import MAX_BYTES, MetadataError
from app.services.validation import load_validation


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate evaluation contracts; no evaluator execution, trust or approval check."
    )
    parser.add_argument("files", nargs="+", help="Local YAML/JSON files, or - for stdin (one document per file)")
    parser.add_argument("--format", choices=("auto", "json", "yaml"), default="auto")
    args = parser.parse_args()
    failed = False
    for index, filename in enumerate(args.files):
        try:
            if filename == "-":
                raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            else:
                with Path(filename).open("rb") as stream:
                    raw = stream.read(MAX_BYTES + 1)
            format = args.format
            if format == "auto" and filename.endswith(".json"):
                format = "json"
            validated = load_validation(raw, format=format)
            print(
                json.dumps(
                    {
                        "input": index,
                        "kind": validated.document.kind,
                        "apiVersion": validated.document.api_version,
                        "digest": validated.digest,
                        "validation": "structure-and-semantics-only",
                    }
                )
            )
        except (OSError, MetadataError) as error:
            failed = True
            message = str(error) if isinstance(error, MetadataError) else "input-unreadable at $"
            print(json.dumps({"input": index, "error": message}), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
