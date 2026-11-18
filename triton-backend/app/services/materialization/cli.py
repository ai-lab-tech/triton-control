"""Offline preparation and init-container entry point; safe diagnostics only."""

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.core.canonical import canonical_bytes, inventory_digest
from app.schemas.materialization import MaterializationPlan, Normalization, StorageBinding
from app.services.candidates.artifacts import DirectorySource
from app.services.materialization.budgets import configured_limits
from app.services.materialization.filesystem import inventory
from app.services.materialization.normalization import normalize
from app.services.materialization.runtime import materialize
from app.services.materialization.s3 import VersionedS3


def read_document(path: str) -> Any:
    with Path(path).open("rb") as stream:
        data = stream.read(1024**2 + 1)
    if len(data) > 1024**2:
        raise ValueError("Document exceeds size limit")

    def distinct(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate document key")
            result[key] = value
        return result

    return json.loads(data, object_pairs_hook=distinct)


def main() -> int:
    parser = argparse.ArgumentParser(description="Immutable repository materializer v1")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    for option in ("plan", "output", "expected-image", "bucket", "prefix", "limits"):
        run.add_argument("--" + option, required=True)
    run.add_argument("--endpoint")
    run.add_argument("--allow-http", action="store_true")
    run.add_argument("--ca-bundle")
    prepare = commands.add_parser("prepare")
    for option in ("source", "normalization", "output", "limits"):
        prepare.add_argument("--" + option, required=True)
    args = parser.parse_args()
    try:
        limits = configured_limits(read_document(args.limits))
        if args.command == "prepare":
            spec = Normalization.model_validate(read_document(args.normalization))
            output = Path(args.output)
            source = Path(args.source)
            if source.resolve().is_relative_to(output.resolve()) or output.resolve().is_relative_to(source.resolve()):
                raise ValueError("Source and output overlap")
            output.mkdir(parents=True, exist_ok=False)
            with tempfile.TemporaryDirectory() as temporary:
                captured = DirectorySource(source).capture(Path(temporary), limits).inventory
                normalize(Path(temporary), output, captured, spec)
            prepared = inventory(output, limits)
            print(
                canonical_bytes(
                    {
                        "source_inventory": captured.model_dump(mode="json"),
                        "prepared_inventory": prepared.model_dump(mode="json"),
                        "source_inventory_digest": inventory_digest(captured),
                        "prepared_inventory_digest": inventory_digest(prepared),
                        "normalization": spec.model_dump(mode="json"),
                    }
                ).decode()
            )
        else:
            import boto3
            from botocore.config import Config

            plan = MaterializationPlan.model_validate(read_document(args.plan))
            binding = StorageBinding(
                bucket=args.bucket,
                prefix=args.prefix,
                secret_name="local-runtime",  # nosec B106 - resource name, not a credential
                endpoint=args.endpoint,
                allow_http=args.allow_http,
            )
            # No ambient instance/workload identity: use only the locally mounted read credential.
            client = boto3.client(
                "s3",
                endpoint_url=binding.endpoint,
                aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
                region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
                verify=args.ca_bundle or True,
                config=Config(
                    connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}, s3={"addressing_style": "path"}
                ),
            )
            try:
                receipt = materialize(
                    plan,
                    VersionedS3(client, binding.bucket, binding.prefix),
                    Path(args.output),
                    expected_image=args.expected_image,
                    limits=limits,
                )
            finally:
                client.close()
            print(canonical_bytes(receipt.model_dump(mode="json")).decode())
        return 0
    except Exception:
        # Storage exceptions may contain endpoint, access-key or configuration contents.
        print("Materialization failed: verify plan, pinned helper, source access and artifact inventories.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
