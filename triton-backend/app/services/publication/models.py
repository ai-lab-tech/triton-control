"""Transfer immutable model versions and verify target bytes before publishing metadata."""

import hashlib
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlsplit

from botocore.exceptions import ClientError

from app.core.canonical import inventory_digest
from app.schemas.materialization import MaterializationPlan, ObjectVersion
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.budgets import check_plan
from app.services.materialization.filesystem import inventory
from app.services.materialization.normalization import normalize
from app.services.materialization.s3 import VersionedS3
from app.services.publication.storage import missing

MULTIPART_BYTES = 64 * 1024**2


def require_versioning(client: Any, bucket: str) -> None:
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise ValueError(f"Versioning required for {bucket}")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("Model publication timed out")


def _upload(client: Any, location: dict[str, str], path: Path, deadline: float) -> str:
    _check_deadline(deadline)
    with path.open("rb") as stream:
        if path.stat().st_size <= MULTIPART_BYTES:
            result = client.put_object(**location, Body=stream, IfNoneMatch="*")
        else:
            upload = client.create_multipart_upload(**location)["UploadId"]
            try:
                parts: list[dict[str, Any]] = []
                part_size = max(MULTIPART_BYTES, (path.stat().st_size + 9999) // 10000)
                while chunk := stream.read(part_size):
                    _check_deadline(deadline)
                    number = len(parts) + 1
                    part = client.upload_part(**location, UploadId=upload, PartNumber=number, Body=chunk)
                    parts.append({"ETag": part["ETag"], "PartNumber": number})
                _check_deadline(deadline)
                result = client.complete_multipart_upload(
                    **location, UploadId=upload, MultipartUpload={"Parts": parts}, IfNoneMatch="*"
                )
            except BaseException:
                client.abort_multipart_upload(**location, UploadId=upload)
                raise
    return str(result["VersionId"])


def promote_models(
    client: Any, origin: str, bucket: str, prefix: str, plan: MaterializationPlan, limits: CaptureLimits
) -> tuple[str, MaterializationPlan]:
    started = time.monotonic()
    deadline = started + limits.deadline_seconds
    source = urlsplit(origin)
    if source.scheme != "s3":
        raise ValueError("A versioned S3 model repository is required")
    check_plan(plan, limits)
    require_versioning(client, bucket)
    destination = prefix + "/models/" + inventory_digest(plan.source_inventory)[7:]
    versions = []
    with TemporaryDirectory() as staging:
        root = Path(staging) / "source"
        root.mkdir()
        reader = VersionedS3(client, source.netloc, source.path.strip("/"), selected_objects=True)
        reader.download(plan, root, limits)
        prepared = Path(staging) / "prepared"
        normalize(root, prepared, plan.source_inventory, plan.normalization)
        if inventory_digest(inventory(prepared, limits)) != inventory_digest(plan.prepared_inventory):
            raise ValueError("Prepared bytes differ from tested inventory")
        for entry in sorted(plan.source_inventory.files, key=lambda item: item.path):
            _check_deadline(deadline)
            location = dict(Bucket=bucket, Key=destination + "/" + entry.path)
            try:
                version = client.head_object(**location)["VersionId"]
            except ClientError as error:
                if not missing(error):
                    raise
                version = _upload(client, location, root / entry.path, deadline)
            reference = ObjectVersion(path=entry.path, version_id=version)
            _check_deadline(deadline)
            copied = client.get_object(**location, VersionId=reference.version_id)
            with copied["Body"] as body:
                if copied["ContentLength"] != entry.size or copied.get("VersionId") != version:
                    raise ValueError("Copied object size or version differs")
                checksum = hashlib.sha256()
                while chunk := body.read(1024**2):
                    _check_deadline(deadline)
                    checksum.update(chunk)
                if "sha256:" + checksum.hexdigest() != entry.digest:
                    raise ValueError("Copied object differs from inventory")
            versions.append(reference)
        if VersionedS3(client, bucket, destination).paths(limits, started) != {v.path for v in versions}:
            raise ValueError("Target model prefix contains unexpected objects")
        _check_deadline(deadline)
    return f"s3://{bucket}/{destination}/", plan.model_copy(update={"objects": tuple(versions)})
