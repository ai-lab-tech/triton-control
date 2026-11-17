"""Capture bounded Triton repository prefixes as inventories of exact S3 versions."""

import time
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError
from sqlmodel import Session

from app.exceptions import BadGatewayError, BadRequestError, ConflictError, ForbiddenError
from app.schemas.handoffs import ResolvedRepository, S3Input, S3RepositoryInput
from app.schemas.lineage import FileEntry
from app.services import handoff_sources
from app.services.storage.s3_client import build_s3_client

MAX_FILES = 998  # Workspace input limit leaves room for dataset and baseline.
MAX_BYTES = 1024**3
MAX_SECONDS = 120
MAX_MANIFEST_BYTES = 512 * 1024


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise BadRequestError("Repository capture exceeded its time limit")


def _scan(client: Any, bucket: str, prefix: str, deadline: float) -> dict[str, tuple[str, int]]:
    """List only descendants; HEAD pins each current object, including empty files."""
    files: dict[str, tuple[str, int]] = {}
    entries, total = 0, 0
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix, PaginationConfig={"PageSize": 100},
    )
    for page in pages:
        _check_deadline(deadline)
        for item in page.get("Contents", []):
            _check_deadline(deadline)
            entries += 1
            if entries > MAX_FILES * 2:
                raise BadRequestError("Repository contains too many entries")
            key = item["Key"]
            if not key.startswith(prefix):
                raise BadRequestError("Repository listing escaped the selected folder")
            relative = key[len(prefix):]
            if key.endswith("/") and item["Size"] == 0:
                continue  # S3 folder markers have no filesystem bytes to restore.
            try:
                FileEntry(path=relative, size=item["Size"], digest="sha256:" + "0" * 64)
            except ValidationError:
                raise BadRequestError("Repository contains an unsafe file path") from None
            if relative in files or len(files) >= MAX_FILES:
                raise BadRequestError("Repository contains duplicate paths or too many files")
            head = client.head_object(Bucket=bucket, Key=key)
            version, size = head.get("VersionId"), head["ContentLength"]
            if not version or version == "null":
                raise BadRequestError("Enable S3 bucket versioning before submitting a repository")
            total += size
            if size < 0 or total > MAX_BYTES:
                raise BadRequestError("Repository exceeds the 1 GiB capture limit")
            files[relative] = (version, size)
    # Prevent a file/directory collision before writing anything into the workspace.
    for name in files:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise BadRequestError("Repository contains a file/directory path collision")
    if not any(len(name.split("/")) == 2 and name.endswith("/config.pbtxt") for name in files):
        raise BadRequestError("Select a Triton repository folder containing model-name/config.pbtxt")
    return files


def resolve(session: Session, source: S3RepositoryInput) -> ResolvedRepository:
    config, bucket, key = handoff_sources.authorize(session, source)
    deadline = time.monotonic() + MAX_SECONDS
    client: Any = None
    try:
        client = build_s3_client(config, client_config=Config(
            connect_timeout=3, read_timeout=10, retries={"max_attempts": 1},
        ))
        selected = _scan(client, bucket, key + "/", deadline)
        captured = []
        for relative, (version, size) in sorted(selected.items()):
            _check_deadline(deadline)
            artifact = handoff_sources.resolve(session, S3Input(
                profile_id=source.profile_id, path=source.path + "/" + relative, version_id=version,
            ), deadline=deadline, allow_empty=True)
            if (artifact.endpoint, artifact.bucket, artifact.key, artifact.file.size) != (
                config["s3_endpoint"], bucket, key + "/" + relative, size,
            ):
                raise ConflictError("Repository source changed during capture; retry after publishing finishes")
            captured.append(artifact.model_copy(update={"file": artifact.file.model_copy(update={"path": relative})}))
        if _scan(client, bucket, key + "/", deadline) != selected:
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
