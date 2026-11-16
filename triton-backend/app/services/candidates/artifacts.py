"""Bounded, single-source capture and immutable artifact providers.

Providers receive already-authorized locations/clients from application services.
They never resolve arbitrary user URLs or execute model code/configuration.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from app.core.canonical import canonical_bytes, inventory_digest
from app.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from app.schemas.candidate_import import ArtifactLocation
from app.schemas.lineage import FileEntry, InventoryV1

CHUNK_SIZE = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class CaptureLimits:
    max_files: int = 10000
    max_bytes: int = 10 * 1024**3
    max_file_bytes: int = 10 * 1024**3
    max_depth: int = 32
    max_config_bytes: int = 1024**2
    deadline_seconds: float = 120

    def __post_init__(self) -> None:
        if any(value <= 0 for value in vars(self).values()):
            raise ValueError("Capture limits must be positive")


@dataclass(frozen=True)
class CapturedSource:
    inventory: InventoryV1
    receipt: dict[str, Any]


class ArtifactSource(ABC):
    @abstractmethod
    def capture(self, destination: Path, limits: CaptureLimits) -> CapturedSource:
        """Write exact source bytes into a new, private staging directory."""


class ArtifactStore(ABC):
    @abstractmethod
    def publish(
        self,
        staging: Path,
        inventory: InventoryV1,
        metadata: InventoryV1,
        manifest: dict[str, Any],
        limits: CaptureLimits,
    ) -> ArtifactLocation:
        """Publish a new snapshot; keep receipts outside the model inventory."""

    @abstractmethod
    def verify(
        self, location: ArtifactLocation, inventory: InventoryV1, metadata: InventoryV1, limits: CaptureLimits
    ) -> None:
        """Verify exact retained bytes before returning a registered import."""


def safe_path(value: str, limits: CaptureLimits) -> str:
    try:
        FileEntry(path=value, size=0, digest="sha256:" + "0" * 64)
    except ValidationError as exc:
        raise BadRequestError("Expected safe, normalized relative artifact paths") from exc
    if len(value.encode("utf-8")) > 1024 or len(value.split("/")) > limits.max_depth:
        raise BadRequestError("Artifact path exceeds capture limits")
    return value


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class _Budget:
    def __init__(self, limits: CaptureLimits) -> None:
        self.limits = limits
        self.started = time.monotonic()
        self.bytes = 0
        self.files = 0
        self.nodes = 0

    def check(self) -> None:
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            raise ConflictError("Artifact capture deadline exceeded")

    def node(self) -> None:
        self.check()
        self.nodes += 1
        if self.nodes > self.limits.max_files * 3:
            raise BadRequestError("Too many artifact paths")

    def copy(self, source: BinaryIO, path: str, destination: Path, expected_size: int) -> FileEntry:
        self.files += 1
        if self.files > self.limits.max_files or expected_size < 0 or expected_size > self.limits.max_file_bytes:
            raise BadRequestError("Artifact file count or size exceeds capture limits")
        if path.endswith("/config.pbtxt") and expected_size > self.limits.max_config_bytes:
            raise BadRequestError("config.pbtxt exceeds capture limits")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with target.open("xb") as output:
            while True:
                self.check()
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                self.bytes += len(chunk)
                if size > self.limits.max_file_bytes or self.bytes > self.limits.max_bytes:
                    raise BadRequestError("Artifact bytes exceed capture limits")
                output.write(chunk)
                digest.update(chunk)
        if size != expected_size:
            raise ConflictError("Artifact size changed during capture")
        return FileEntry(path=path, size=size, digest="sha256:" + digest.hexdigest())


def _open_directory(path: Path) -> int:
    # Anchor every component with dir_fd + O_NOFOLLOW: checking is_symlink then
    # opening by pathname would permit a parent-directory replacement race.
    absolute = path.absolute()
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            if component in {".", ".."}:
                raise BadRequestError("Directory source must not contain relative escapes")
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class DirectorySource(ArtifactSource):
    """Trusted server-side directory only; the caller must authorize its root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def capture(self, destination: Path, limits: CaptureLimits) -> CapturedSource:
        budget = _Budget(limits)
        files: list[FileEntry] = []
        seen: dict[str, tuple[int, ...]] = {}

        def walk(fd: int, prefix: str, *, copy: bool) -> dict[str, tuple[int, ...]]:
            signatures: dict[str, tuple[int, ...]] = {}
            names: list[str] = []
            with os.scandir(fd) as iterator:
                for entry in iterator:
                    # Bound enumeration itself, before buffering or sorting names.
                    # This also applies to the final source-consistency scan.
                    budget.node()
                    names.append(entry.name)
            names.sort()
            budget.check()
            for name in names:
                budget.check()
                relative = safe_path(prefix + name, limits)
                before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                signatures[relative] = _signature(before)
                if stat.S_ISDIR(before.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        if _signature(os.fstat(child)) != _signature(before):
                            raise ConflictError("Source directory changed during capture")
                        signatures.update(walk(child, relative + "/", copy=copy))
                    finally:
                        os.close(child)
                elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                    if copy:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                        with os.fdopen(file_fd, "rb") as stream:
                            if _signature(os.fstat(stream.fileno())) != _signature(before):
                                raise ConflictError("Source file changed before capture")
                            files.append(budget.copy(stream, relative, destination, before.st_size))
                            if _signature(os.fstat(stream.fileno())) != _signature(before):
                                raise ConflictError("Source file changed during capture")
                else:
                    raise BadRequestError("Links, hard links and special files are unsupported")
            return signatures

        try:
            fd = _open_directory(self.root)
            try:
                seen = walk(fd, "", copy=True)
                budget.nodes = 0
                if walk(fd, "", copy=False) != seen:
                    raise ConflictError("Source changed during capture")
                # Detect renaming/replacing the root while its descriptor stayed open.
                current = _open_directory(self.root)
                try:
                    if (os.fstat(current).st_dev, os.fstat(current).st_ino) != (
                        os.fstat(fd).st_dev,
                        os.fstat(fd).st_ino,
                    ):
                        raise ConflictError("Source root was replaced during capture")
                finally:
                    os.close(current)
            finally:
                os.close(fd)
        except OSError as exc:
            raise BadRequestError("Source is unavailable, unsafe or changed during capture") from exc
        return CapturedSource(InventoryV1(files=tuple(files)), {"kind": "directory"})


class S3Source(ArtifactSource):
    """One authorized bucket/prefix, with version or conditional reads and rechecks."""

    def __init__(self, client: Any, bucket: str, prefix: str) -> None:
        self.client, self.bucket = client, bucket
        if prefix:
            safe_path(prefix.removesuffix("/"), CaptureLimits())
        self.prefix = prefix.removesuffix("/") + "/" if prefix else ""

    def _listing(self, limits: CaptureLimits, budget: _Budget) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        budget.nodes = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix, PaginationConfig={"PageSize": 1000}):
            budget.check()
            for item in page.get("Contents", []):
                budget.node()
                key = item["Key"]
                if not key.startswith(self.prefix):
                    raise BadRequestError("S3 source returned an unrelated key")
                relative = key[len(self.prefix) :]
                if not relative and item["Size"] == 0:
                    continue
                if relative.endswith("/") and item["Size"] == 0:
                    safe_path(relative[:-1], limits)
                    continue
                safe_path(relative, limits)
                if relative in result:
                    raise BadRequestError("Duplicate S3 source path")
                if len(result) >= limits.max_files:
                    raise BadRequestError("S3 source exceeds file-count limits")
                result[relative] = {"key": key, "size": item["Size"], "etag": item["ETag"]}
        if sum(item["size"] for item in result.values()) > limits.max_bytes:
            raise BadRequestError("S3 source exceeds capture byte limits")
        return result

    def capture(self, destination: Path, limits: CaptureLimits) -> CapturedSource:
        budget = _Budget(limits)
        entries: list[FileEntry] = []
        versions: dict[str, dict[str, Any]] = {}
        try:
            listing = self._listing(limits, budget)
            for path, item in sorted(listing.items()):
                budget.check()
                head = self.client.head_object(Bucket=self.bucket, Key=item["key"])
                if head["ContentLength"] != item["size"] or head["ETag"] != item["etag"]:
                    raise ConflictError("S3 source changed before capture")
                version = head.get("VersionId")
                args = {"VersionId": version} if version and version != "null" else {"IfMatch": item["etag"]}
                response = self.client.get_object(Bucket=self.bucket, Key=item["key"], **args)
                stream = response["Body"]
                try:
                    entries.append(budget.copy(stream, path, destination, item["size"]))
                finally:
                    stream.close()
                versions[path] = {**item, "version_id": version}
            if self._listing(limits, budget) != listing:
                raise ConflictError("S3 source changed during capture")
            for item in versions.values():
                budget.check()
                current = self.client.head_object(Bucket=self.bucket, Key=item["key"])
                if (current.get("VersionId"), current["ETag"], current["ContentLength"]) != (
                    item["version_id"],
                    item["etag"],
                    item["size"],
                ):
                    raise ConflictError("S3 source changed during capture")
        except OSError as exc:
            raise BadRequestError("S3 objects contain conflicting file/directory paths") from exc
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"PreconditionFailed", "412", "NoSuchKey", "NoSuchVersion"}:
                raise ConflictError("S3 source changed or disappeared during capture") from exc
            raise ServiceUnavailableError("S3 source capture failed") from exc
        except BotoCoreError as exc:
            raise ServiceUnavailableError("S3 source capture failed") from exc
        return CapturedSource(
            InventoryV1(files=tuple(entries)),
            {
                "kind": "s3",
                "bucket": self.bucket,
                "prefix": self.prefix,
                "objects": versions,
            },
        )


def _manifest(inventory: InventoryV1, metadata: InventoryV1, manifest: dict[str, Any]) -> bytes:
    data = canonical_bytes(
        {
            **manifest,
            "inventory": inventory.model_dump(mode="json"),
            "metadata_inventory": metadata.model_dump(mode="json"),
        }
    )
    if len(data) > MAX_MANIFEST_BYTES:
        raise BadRequestError("Candidate manifest exceeds storage limits")
    return data


def _area_files(inventory: InventoryV1, metadata: InventoryV1) -> list[tuple[str, FileEntry]]:
    return [("repository", entry) for entry in inventory.files] + [
        ("source-metadata", entry) for entry in metadata.files
    ]


class DirectoryArtifactStore(ArtifactStore):
    """Private, persistent server-side store; all replicas must share this root."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def publish(
        self,
        staging: Path,
        inventory: InventoryV1,
        metadata: InventoryV1,
        manifest: dict[str, Any],
        limits: CaptureLimits,
    ) -> ArtifactLocation:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = _open_directory(self.root)
        os.close(fd)
        snapshot = str(uuid4())
        temporary = Path(tempfile.mkdtemp(prefix=".capture-", dir=self.root))
        data = _manifest(inventory, metadata, manifest)
        try:
            budget = _Budget(limits)
            for area, entry in _area_files(inventory, metadata):
                with (staging / entry.path).open("rb") as source:
                    measured = budget.copy(source, area + "/" + entry.path, temporary, entry.size)
                if measured.digest != entry.digest:
                    raise ConflictError("Staged artifact changed before storage")
                with (temporary / area / entry.path).open("rb") as output:
                    os.fsync(output.fileno())
            (temporary / "repository").mkdir(exist_ok=True)
            (temporary / "source-metadata").mkdir(exist_ok=True)
            with (temporary / "manifest.json").open("xb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            for directory in [path for path in temporary.rglob("*") if path.is_dir()] + [temporary]:
                directory_fd = _open_directory(directory)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            os.rename(temporary, self.root / snapshot)
            root_fd = _open_directory(self.root)
            try:
                os.fsync(root_fd)
            finally:
                os.close(root_fd)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        location = ArtifactLocation(
            kind="directory", reference={"snapshot_id": snapshot, "manifest_digest": _sha(data)}
        )
        self.verify(location, inventory, metadata, limits)
        for path in (self.root / snapshot).rglob("*"):
            path.chmod(0o555 if path.is_dir() else 0o444)
        (self.root / snapshot).chmod(0o555)
        return location

    def verify(
        self, location: ArtifactLocation, inventory: InventoryV1, metadata: InventoryV1, limits: CaptureLimits
    ) -> None:
        if location.kind != "directory":
            raise BadRequestError("Artifact store does not match the recorded provider")
        try:
            snapshot = str(UUID(location.reference["snapshot_id"]))
            root = self.root / snapshot
            # The configured store is private; still reject link replacement during reads.
            fd = _open_directory(root)
            try:
                manifest_fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                with os.fdopen(manifest_fd, "rb") as stream:
                    data = stream.read(MAX_MANIFEST_BYTES + 1)
                if _sha(data) != location.reference["manifest_digest"]:
                    raise ConflictError("Stored manifest does not match its digest")
            finally:
                os.close(fd)
            for area, expected in (("repository", inventory), ("source-metadata", metadata)):
                with tempfile.TemporaryDirectory(prefix="candidate-verify-") as directory:
                    actual = DirectorySource(root / area).capture(Path(directory), limits).inventory
                if inventory_digest(actual) != inventory_digest(expected):
                    raise ConflictError("Stored artifact inventory does not match")
        except (OSError, KeyError, ValueError) as exc:
            raise ConflictError("Stored candidate artifacts are unavailable or invalid") from exc


class S3ArtifactStore(ArtifactStore):
    """Version-pinned snapshots in an administrator-selected, versioned bucket."""

    def __init__(self, client: Any, bucket: str, prefix: str = "candidate-imports") -> None:
        self.client, self.bucket = client, bucket
        self.prefix = safe_path(prefix.removesuffix("/"), CaptureLimits())

    def publish(
        self,
        staging: Path,
        inventory: InventoryV1,
        metadata: InventoryV1,
        manifest: dict[str, Any],
        limits: CaptureLimits,
    ) -> ArtifactLocation:
        if self.client.get_bucket_versioning(Bucket=self.bucket).get("Status") != "Enabled":
            raise BadRequestError("Candidate artifact bucket must have versioning enabled")
        if any(entry.size > 5 * 1024**3 for _, entry in _area_files(inventory, metadata)):
            raise BadRequestError("S3 artifact storage currently supports files up to 5 GiB")
        budget = _Budget(limits)
        prefix = f"{self.prefix}/{uuid4()}"
        objects = []
        for area, entry in _area_files(inventory, metadata):
            budget.check()
            key = f"{prefix}/{area}/{entry.path}"
            with (staging / entry.path).open("rb") as stream:
                response = self.client.put_object(Bucket=self.bucket, Key=key, Body=stream, ContentLength=entry.size)
            version = response.get("VersionId")
            if not version or version == "null":
                raise ConflictError("Artifact write did not return an immutable object version")
            objects.append({"area": area, "path": entry.path, "key": key, "version_id": version})
        data = _manifest(inventory, metadata, manifest)
        key = prefix + "/manifest.json"
        response = self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType="application/json")
        if not response.get("VersionId") or response["VersionId"] == "null":
            raise ConflictError("Manifest write did not return an immutable object version")
        location = ArtifactLocation(
            kind="s3",
            reference={
                "bucket": self.bucket,
                "prefix": prefix,
                "objects": objects,
                "manifest_key": key,
                "manifest_version_id": response["VersionId"],
                "manifest_digest": _sha(data),
            },
        )
        self.verify(location, inventory, metadata, limits)
        return location

    def verify(
        self, location: ArtifactLocation, inventory: InventoryV1, metadata: InventoryV1, limits: CaptureLimits
    ) -> None:
        ref = location.reference
        if (
            location.kind != "s3"
            or ref.get("bucket") != self.bucket
            or not ref.get("prefix", "").startswith(self.prefix + "/")
        ):
            raise BadRequestError("Artifact store does not match the recorded provider")
        expected = {(area, entry.path): entry for area, entry in _area_files(inventory, metadata)}
        actual = {(item["area"], item["path"]) for item in ref["objects"]}
        if set(expected) != actual or len(actual) != len(ref["objects"]):
            raise ConflictError("Stored object references do not match the inventory")
        budget = _Budget(limits)
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=ref["manifest_key"], VersionId=ref["manifest_version_id"]
            )
            try:
                data = response["Body"].read(MAX_MANIFEST_BYTES + 1)
            finally:
                response["Body"].close()
            if _sha(data) != ref["manifest_digest"]:
                raise ConflictError("Stored manifest does not match its digest")
            with tempfile.TemporaryDirectory(prefix="candidate-verify-") as directory:
                for item in ref["objects"]:
                    entry = expected[(item["area"], item["path"])]
                    if item["key"] != f"{ref['prefix']}/{item['area']}/{entry.path}":
                        raise ConflictError("Stored object escaped the snapshot prefix")
                    response = self.client.get_object(Bucket=self.bucket, Key=item["key"], VersionId=item["version_id"])
                    try:
                        measured = budget.copy(
                            response["Body"], item["area"] + "/" + entry.path, Path(directory), entry.size
                        )
                    finally:
                        response["Body"].close()
                    if measured.digest != entry.digest:
                        raise ConflictError("Stored artifact does not match its digest")
        except (BotoCoreError, ClientError) as exc:
            raise ServiceUnavailableError("Stored S3 snapshot could not be verified") from exc
