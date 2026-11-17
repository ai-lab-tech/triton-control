"""Authorized, bounded capture of exact versioned S3 object identities."""

import hashlib
import time
from pathlib import Path
from typing import Any
from unicodedata import normalize

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session

from app.exceptions import BadGatewayError, BadRequestError, ForbiddenError
from app.repositories import s3_profiles
from app.schemas.handoffs import ResolvedInput, S3Input
from app.schemas.lineage import FileEntry
from app.services.storage.s3_client import S3Config, build_s3_client

MAX_BYTES = 1024 * 1024 * 1024
MAX_SECONDS = 120


def authorize(session: Session, source: S3Input) -> tuple[S3Config, str, str]:
    profile = s3_profiles.get(session, source.profile_id)
    if profile is None:
        raise ForbiddenError("Source profile is unavailable")
    session.refresh(profile)
    path = source.path
    if (
        path.startswith("/")
        or "\\" in path
        or ":" in path
        or path != normalize("NFC", path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise BadRequestError("Source path must be relative to the configured profile prefix")
    prefix = (profile.prefix or "").strip("/")
    key = f"{prefix}/{path}" if prefix else path
    if len(key.encode("utf-8")) > 1024:
        raise BadRequestError("Source object key exceeds the S3 length limit")
    return (
        S3Config(
            s3_endpoint=profile.endpoint,
            s3_bucket=profile.bucket,
            s3_access_key=profile.access_key,
            s3_secret_key_enc=profile.secret_key_enc,
            s3_region=profile.region,
            s3_use_https=profile.endpoint.startswith("https://"),
            s3_verify_ssl=True,
            s3_ca_certificate=profile.ca_certificate or "",
            s3_address_style="path" if profile.force_path_style else "auto",
        ),
        profile.bucket,
        key,
    )


def resolve(session: Session, source: S3Input) -> ResolvedInput:
    config, bucket, key = authorize(session, source)
    client: Any = None
    started = time.monotonic()
    body: Any = None
    try:
        client = build_s3_client(
            config, client_config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1})
        )
        args: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if source.version_id:
            args["VersionId"] = source.version_id
        head = client.head_object(**args)
        version = head.get("VersionId")
        if not version or version == "null":
            raise BadRequestError("Enable S3 bucket versioning before submitting immutable handoff inputs")
        size = head["ContentLength"]
        if size < 1 or size > MAX_BYTES:
            raise BadRequestError("Handoff input must contain between 1 byte and 1 GiB")
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version)
        body = response["Body"]
        measured, checksum = 0, hashlib.sha256()
        while True:
            if time.monotonic() - started > MAX_SECONDS:
                raise BadRequestError("Handoff input capture exceeded its time limit")
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            measured += len(chunk)
            if measured > MAX_BYTES or measured > size:
                raise BadRequestError("Handoff input exceeded its size limit")
            checksum.update(chunk)
        actual = "sha256:" + checksum.hexdigest()
        if measured != size or response.get("VersionId") != version:
            raise BadRequestError("Handoff input changed during capture")
        if source.expected_digest is not None and actual != source.expected_digest:
            raise BadRequestError("Handoff input checksum does not match")
        return ResolvedInput(
            source=source,
            bucket=bucket,
            key=key,
            endpoint=config["s3_endpoint"] or "",
            version_id=version,
            file=FileEntry(path=source.path, size=measured, digest=actual),
        )
    except ClientError as exc:
        if str(exc.response.get("Error", {}).get("Code")) in {
            "AccessDenied",
            "403",
            "NoSuchKey",
            "NoSuchVersion",
            "404",
        }:
            raise ForbiddenError("Source object is unavailable or access is denied") from None
        raise BadGatewayError("Source storage could not be read") from None
    except (BotoCoreError, OSError, RuntimeError):
        raise BadGatewayError("Source storage could not be read") from None
    finally:
        if body is not None:
            body.close()
        if client is not None:
            client.close()


def download(session: Session, artifact: ResolvedInput, destination: Path) -> None:
    """Read only the captured version; verify before exposing any bytes to a workspace."""
    config, bucket, key = authorize(session, artifact.source)
    if (config["s3_endpoint"], bucket, key) != (artifact.endpoint, artifact.bucket, artifact.key):
        raise ForbiddenError("Captured source profile changed")
    if not artifact.version_id or artifact.version_id == "null" or not 0 < artifact.file.size <= MAX_BYTES:
        raise BadRequestError("Workspace input requires a bounded, versioned artifact")
    client: Any = None
    body: Any = None
    try:
        client = build_s3_client(config, client_config=Config(
            connect_timeout=3, read_timeout=10, retries={"max_attempts": 1},
        ))
        response = client.get_object(Bucket=bucket, Key=key, VersionId=artifact.version_id)
        body = response["Body"]
        measured, checksum = 0, hashlib.sha256()
        started = time.monotonic()
        with destination.open("wb") as output:
            while True:
                if time.monotonic() - started > MAX_SECONDS:
                    raise BadRequestError("Workspace input download exceeded its time limit")
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                measured += len(chunk)
                if measured > artifact.file.size:
                    raise BadRequestError("Workspace input size does not match its snapshot")
                checksum.update(chunk)
                output.write(chunk)
        if (measured != artifact.file.size or "sha256:" + checksum.hexdigest() != artifact.file.digest
                or response.get("VersionId") != artifact.version_id):
            raise BadRequestError("Workspace input hash or version does not match its snapshot")
    except (BotoCoreError, ClientError, OSError):
        raise BadGatewayError("Workspace input storage is unavailable") from None
    finally:
        if body is not None:
            body.close()
        if client is not None:
            client.close()
