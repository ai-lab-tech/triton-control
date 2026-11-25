"""Publication providers. All metadata mutations use compare-and-swap."""

import hashlib
import os
import shutil
import subprocess  # nosec B404 - Git CLI is required for branch publication.
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from app.services.serving_metadata import load_metadata


def run_git(*args: str, cwd: Path | None = None) -> bytes:
    executable = shutil.which("git", path=os.defpath)
    if executable is None:
        raise FileNotFoundError("Git is required for release publication")
    # Only built-in publication transports are allowed. All arguments stay separate.
    env = {**os.environ, "GIT_ALLOW_PROTOCOL": "file:https:ssh"}
    return subprocess.run(  # nosec B603 - system executable, fixed subcommands, no shell.
        [executable, *args], cwd=cwd, env=env, check=True, stdout=subprocess.PIPE,
    ).stdout


def checksum(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class MetadataStore(ABC):
    def write(self, key: str, data: bytes) -> None:
        self._write(key, data, None)

    def write_target(self, key: str, data: bytes, previous: str | None) -> None:
        self._write(key, data, previous)

    @abstractmethod
    def _write(self, key: str, data: bytes, previous: str | None) -> None: ...


def _requires_write(key: str, current: bytes | None, data: bytes, previous: str | None) -> bool:
    if current == data:
        return False
    if current is None:
        if previous is None:
            return True
    elif previous is not None and load_metadata(current).digest == previous:
        return True
    raise ValueError(f"Publication conflict at {key}")


class GitStore(MetadataStore):
    """A private temporary clone of the publisher-selected branch."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _write(self, key: str, data: bytes, previous: str | None) -> None:
        path = self.root / key
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(self.root)):
            raise ValueError("Symlink in publication path")
        current = path.read_bytes() if path.exists() else None
        if not _requires_write(key, current, data, previous):
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        run_git("add", "--", key, cwd=self.root)
        run_git("commit", "-m", f"Publish {key}", cwd=self.root)
        blob = run_git("show", f"HEAD:{key}", cwd=self.root)
        if blob != data:
            raise ValueError("Committed bytes differ from publication input")


def missing(error: ClientError) -> bool:
    return error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}


class S3Store(MetadataStore):
    def __init__(self, client: Any, bucket: str, prefix: str) -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix.rstrip("/") + "/"

    def _write(self, key: str, data: bytes, previous: str | None) -> None:
        location = dict(Bucket=self.bucket, Key=self.prefix + key)
        try:
            found = self.client.get_object(**location)
        except ClientError as error:
            if not missing(error):
                raise
            current = None
            condition = {"IfNoneMatch": "*"}
        else:
            with found["Body"] as stream:
                current = stream.read(max(len(data), 4 * 1024**2) + 1)
            condition = {"IfMatch": found["ETag"]}
        if not _requires_write(key, current, data, previous):
            return
        written = self.client.put_object(**location, Body=data, **condition)
        version = written.get("VersionId")
        if not version or version == "null":
            raise ValueError("Publication requires S3 versioning")
        with self.client.get_object(**location, VersionId=version)["Body"] as stream:
            if stream.read(len(data) + 1) != data:
                raise ValueError("Published metadata verification failed")
