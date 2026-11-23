"""Publication providers. All metadata mutations use compare-and-swap."""

import hashlib
import os
import shutil
import subprocess  # nosec B404 - Git CLI is required for branch publication.
from pathlib import Path
from typing import Any, Protocol

from botocore.exceptions import ClientError


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


class MetadataStore(Protocol):
    def write(self, key: str, data: bytes, previous: str | None = None) -> None: ...


class GitStore:
    """A private temporary clone of the publisher-selected branch."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, key: str, data: bytes, previous: str | None = None) -> None:
        path = self.root / key
        if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(self.root)):
            raise ValueError("Symlink in publication path")
        current = path.read_bytes() if path.exists() else None
        if current == data:
            return
        if (checksum(current) if current is not None else None) != previous:
            raise ValueError(f"Publication conflict at {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        run_git("add", "--", key, cwd=self.root)
        run_git("commit", "-m", f"Publish {key}", cwd=self.root)
        blob = run_git("show", f"HEAD:{key}", cwd=self.root)
        if blob != data:
            raise ValueError("Committed bytes differ from publication input")


def missing(error: ClientError) -> bool:
    return error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}


class S3Store:
    def __init__(self, client: Any, bucket: str, prefix: str) -> None:
        self.client, self.bucket, self.prefix = client, bucket, prefix.rstrip("/") + "/"

    def write(self, key: str, data: bytes, previous: str | None = None) -> None:
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
        if current == data:
            return
        if (checksum(current) if current is not None else None) != previous:
            raise ValueError(f"Publication conflict at {key}")
        written = self.client.put_object(**location, Body=data, **condition)
        version = written.get("VersionId")
        if not version or version == "null":
            raise ValueError("Publication requires S3 versioning")
        with self.client.get_object(**location, VersionId=version)["Body"] as stream:
            if stream.read(len(data) + 1) != data:
                raise ValueError("Published metadata verification failed")
