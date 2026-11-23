"""Publish from an operator-owned bundle to a versioned bucket or a Git branch."""

import argparse
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from pydantic import TypeAdapter

from app.schemas.lineage import Digest
from app.schemas.serving_metadata import Path as MetadataPath
from app.services.materialization.budgets import configured_limits
from app.services.materialization.cli import read_document
from app.services.publication.models import require_versioning
from app.services.publication.service import publish
from app.services.publication.storage import GitStore, S3Store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--model-bucket", required=True)
    parser.add_argument("--model-prefix", required=True)
    parser.add_argument("--limits", required=True)
    parser.add_argument("--previous-target", help="Digest of the target being replaced (default: create only)")
    commands = parser.add_subparsers(dest="provider", required=True)
    git = commands.add_parser("git")
    git.add_argument("repository")
    git.add_argument("branch")
    s3 = commands.add_parser("s3")
    s3.add_argument("bucket")
    s3.add_argument("prefix")
    args = parser.parse_args()
    limits = configured_limits(read_document(args.limits))
    prefix: str = TypeAdapter(MetadataPath).validate_python(args.model_prefix)
    if args.previous_target:
        TypeAdapter(Digest).validate_python(args.previous_target)
    client = boto3.client("s3")
    if args.provider == "s3":
        require_versioning(client, args.bucket)
        metadata_prefix: str = TypeAdapter(MetadataPath).validate_python(args.prefix)
        store = S3Store(client, args.bucket, metadata_prefix)
        result = publish(args.bundle, store, client, args.model_bucket, prefix, limits, args.previous_target)
    else:
        branch = args.branch
        subprocess.run(["git", "check-ref-format", "--branch", branch], check=True)
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "clone", "--single-branch", "--branch", branch, "--",
                            args.repository, temporary], check=True)
            result = publish(
                args.bundle, GitStore(root), client, args.model_bucket, prefix, limits, args.previous_target
            )
            subprocess.run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], cwd=root, check=True)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
