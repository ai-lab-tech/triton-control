"""Capture bounded Triton repository prefixes as inventories of verified bytes."""

import time
from typing import Any, Callable, NamedTuple

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError
from sqlmodel import Session

from app.exceptions import BadGatewayError, BadRequestError, ConflictError, ForbiddenError
from app.schemas.handoffs import ResolvedRepository, S3Input, S3RepositoryInput
from app.schemas.lineage import FileEntry
from app.schemas.serving_metadata import StartupModel
from app.services import handoff_sources
from app.services.preparation_settings import effective
from app.services.storage.s3_client import build_s3_client

MAX_FILES = 998  # Workspace input limit leaves room for dataset and baseline.
MAX_MANIFEST_BYTES = 512 * 1024


class ListedObject(NamedTuple):
    version: str | None
    size: int
    etag: str | None


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise BadRequestError("Repository capture exceeded its time limit")


def _scan(client: Any, bucket: str, prefix: str, deadline: float, max_bytes: int,
          checkpoint: Callable[[int], None], *, require_versioning: bool,
          model: StartupModel | None = None) -> dict[str, ListedObject]:
    """List only descendants and record each current object's identity."""
    files: dict[str, ListedObject] = {}
    entries, total = 0, 0
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix, PaginationConfig={"PageSize": 100},
    )
    for page in pages:
        _check_deadline(deadline)
        for item in page.get("Contents", []):
            _check_deadline(deadline)
            checkpoint(0)
            entries += 1
            if entries > MAX_FILES * 2:
                raise BadRequestError("Repository contains too many entries")
            key = item["Key"]
            if not key.startswith(prefix):
                raise BadRequestError("Repository listing escaped the selected folder")
            relative = key[len(prefix):]
            if key.endswith("/") and item["Size"] == 0:
                continue  # S3 folder markers have no filesystem bytes to restore.
            if model:
                directory = relative.split("/", 1)[0]
                if directory.isdigit() and directory not in model.versions:
                    continue
            try:
                FileEntry(path=relative, size=item["Size"], digest="sha256:" + "0" * 64)
            except ValidationError:
                raise BadRequestError("Repository contains an unsafe file path") from None
            if relative in files or len(files) >= MAX_FILES:
                raise BadRequestError("Repository contains duplicate paths or too many files")
            head = client.head_object(Bucket=bucket, Key=key)
            version, size = handoff_sources.version_id(head), head["ContentLength"]
            if version is None and require_versioning:
                raise BadRequestError("Enable S3 bucket versioning before submitting a repository")
            etag = head.get("ETag") if version is None else None
            if version is None and not etag:
                raise BadRequestError("Source storage must provide an ETag for unversioned repository files")
            total += size
            if size < 0 or total > max_bytes:
                raise BadRequestError("Repository exceeds the configured capture size limit")
            files[relative] = ListedObject(version, size, etag)
    # Prevent a file/directory collision before writing anything into the workspace.
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise BadRequestError("Repository contains a file/directory path collision")
    if model:
        if "config.pbtxt" not in files or any(
            not any(path.startswith(version + "/") for path in files) for version in model.versions
        ):
            raise BadRequestError("The selected model configuration or version is missing from the S3 repository")
    elif not any(len(name.split("/")) == 2 and name.endswith("/config.pbtxt") for name in files):
        raise BadRequestError("Select a Triton repository folder containing model-name/config.pbtxt")
    return files


def resolve(session: Session, source: S3RepositoryInput, *,
            checkpoint: Callable[[int], None] = lambda _: None) -> ResolvedRepository:
    config, bucket, key = handoff_sources.authorize(session, source)
    limits = effective(session)
    deadline = time.monotonic() + limits.transfer_timeout_seconds
    max_bytes = limits.max_input_gib * 1024**3
    client: Any = None
    try:
        client = build_s3_client(config, client_config=Config(
            connect_timeout=3, read_timeout=10, retries={"max_attempts": 1},
        ))
        root_prefix = key + "/" if key else ""
        model_folder = source.model.name + "/" if source.model else ""
        if source.model:
            # Managed S3 sync also accepts a prefix rooted at config.pbtxt.
            root_config = root_prefix + "config.pbtxt"
            page = client.list_objects_v2(Bucket=bucket, Prefix=root_config, MaxKeys=1)
            if any(item["Key"] == root_config for item in page.get("Contents", [])):
                model_folder = ""
        prefix = root_prefix + model_folder
        selected = _scan(client, bucket, prefix, deadline, max_bytes, checkpoint,
                         require_versioning=limits.require_s3_versioning, model=source.model)
        captured = []
        for relative, (version, size, etag) in sorted(selected.items()):
            local_path = f"{source.model.name}/{relative}" if source.model else relative
            source_file = model_folder + relative
            path = source_file if source.path == "." else source.path + "/" + source_file
            _check_deadline(deadline)
            artifact = handoff_sources.resolve(session, S3Input(
                profile_id=source.profile_id, path=path, version_id=version,
            ), deadline=deadline, allow_empty=True, expected_etag=etag, checkpoint=checkpoint)
            if (artifact.endpoint, artifact.bucket, artifact.key, artifact.file.size, artifact.version_id) != (
                config["s3_endpoint"], bucket, prefix + relative, size, version,
            ):
                raise ConflictError("Repository source changed during capture; retry after publishing finishes")
            captured.append(artifact.model_copy(update={"file": artifact.file.model_copy(update={"path": local_path})}))
        if _scan(client, bucket, prefix, deadline, max_bytes, checkpoint,
                 require_versioning=limits.require_s3_versioning, model=source.model) != selected:
            raise ConflictError("Repository changed during capture; retry after publishing finishes")
        result = ResolvedRepository(source=source, endpoint=config["s3_endpoint"] or "",
                                    bucket=bucket, key=key, files=captured)
        if len(result.model_dump_json().encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise BadRequestError("Repository inventory exceeds the 512 KiB metadata limit")
        return result
    except ValidationError:
        raise BadRequestError("Repository contains an invalid object path") from None
    except ClientError as error:
        if str(error.response.get("Error", {}).get("Code")) in {"AccessDenied", "403", "NoSuchKey", "404"}:
            raise ForbiddenError("Repository is unavailable or access is denied") from None
        raise BadGatewayError("Repository storage could not be read") from None
    except (BotoCoreError, OSError):
        raise BadGatewayError("Repository storage could not be read") from None
    finally:
        if client is not None:
            client.close()
