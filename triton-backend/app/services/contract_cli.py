"""Shared offline contract CLI with bounded input and safe diagnostics."""

import argparse
import json
import sys
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.services.contract_io import MAX_BYTES, Format, MetadataError, ValidatedContract

DocumentT = TypeVar("DocumentT", bound=BaseModel)


class ContractLoader(Protocol[DocumentT]):
    def __call__(self, raw: bytes, *, format: Format) -> ValidatedContract[DocumentT]: ...


def run_cli(loader: ContractLoader[DocumentT], *, description: str) -> int:
    parser = argparse.ArgumentParser(description=description)
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
            validated = loader(raw, format=format)
            document = validated.document.model_dump(mode="json", by_alias=True)
            print(
                json.dumps(
                    {
                        "input": index,
                        "kind": document["kind"],
                        "apiVersion": document["apiVersion"],
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
