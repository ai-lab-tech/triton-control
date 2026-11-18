"""Read-only, version-pinned S3 provider for an already authorized prefix."""

import hashlib
import time
from pathlib import Path
from typing import Any

from app.schemas.materialization import MaterializationPlan
from app.services.candidates.artifacts import CaptureLimits, safe_path
from app.services.materialization.normalization import MaterializationError


class VersionedS3:
    def __init__(self, client: Any, bucket: str, prefix: str) -> None:
        self.client, self.bucket = client, bucket
        self.prefix = safe_path(prefix.removesuffix("/"), CaptureLimits()) + "/"

    def paths(self, limits: CaptureLimits, started: float) -> set[str]:
        paths: set[str] = set()
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket,
            Prefix=self.prefix,
            PaginationConfig={"PageSize": 1000},
        ):
            if time.monotonic() - started > limits.deadline_seconds:
                raise MaterializationError("Repository listing timed out")
            for item in page.get("Contents", []):
                key = item["Key"]
                if not key.startswith(self.prefix):
                    raise MaterializationError("Storage returned an unrelated key")
                path = safe_path(key[len(self.prefix) :], limits)
                if path in paths or len(paths) >= limits.max_files:
                    raise MaterializationError("Duplicate or excessive repository paths")
                paths.add(path)
        return paths

    def download(self, plan: MaterializationPlan, destination: Path, limits: CaptureLimits) -> None:
        started = time.monotonic()
        expected = {entry.path: entry for entry in plan.source_inventory.files}
        if sum(entry.size for entry in expected.values()) > limits.max_bytes:
            raise MaterializationError("Repository exceeds byte budget")
        if self.paths(limits, started) != set(expected):
            raise MaterializationError("Repository contains missing or extra files")
        for reference in sorted(plan.objects, key=lambda item: item.path):
            entry = expected[reference.path]
            if entry.size > limits.max_file_bytes:
                raise MaterializationError("Object exceeds file budget")
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.prefix + reference.path, VersionId=reference.version_id
            )
            body = response["Body"]
            try:
                if response.get("VersionId") != reference.version_id or response.get("ContentLength") != entry.size:
                    raise MaterializationError("Object version or size differs from manifest")
                target = destination / reference.path
                target.parent.mkdir(parents=True, exist_ok=True)
                checksum, size = hashlib.sha256(), 0
                with target.open("xb") as output:
                    while chunk := body.read(1024**2):
                        if time.monotonic() - started > limits.deadline_seconds:
                            raise MaterializationError("Repository download timed out")
                        size += len(chunk)
                        if size > entry.size:
                            raise MaterializationError("Object exceeds its declared size")
                        checksum.update(chunk)
                        output.write(chunk)
                if size != entry.size or "sha256:" + checksum.hexdigest() != entry.digest:
                    raise MaterializationError("Object content differs from inventory")
            finally:
                body.close()
        if self.paths(limits, started) != set(expected):
            raise MaterializationError("Repository changed during download")
