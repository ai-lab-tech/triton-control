"""Restore retained experiment bytes into task workspaces, without recapture."""

import hashlib
import os
import stat
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from uuid import UUID

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from sqlmodel import Session

from app.db.entities import PreparationSnapshotEntity
from app.exceptions import ConflictError, ForbiddenError
from app.repositories import experiment_snapshots, task_workspaces
from app.schemas.experiment_snapshots import SnapshotManifest
from app.schemas.handoffs import S3Input
from app.schemas.lineage import Snapshot
from app.schemas.task_workspaces import SnapshotFile, WorkspaceInput, WorkspaceInputs
from app.services import handoff_sources
from app.services.candidates.artifacts import _open_directory
from app.services.preparation_settings import effective
from app.services.snapshots.artifacts import store_settings
from app.services.storage.s3_client import build_s3_client


def register(session: Session, row: PreparationSnapshotEntity) -> None:
    manifest = SnapshotManifest.model_validate(row.manifest)
    files = (*manifest.inventory.files, *manifest.workspace_inventory.files)
    if not files:
        return  # An empty capture remains explicitly blocked by its recorded gaps.
    task_workspaces.register_snapshot_inputs(session, row.id, WorkspaceInputs(
        snapshot=manifest.source_snapshot,
        files=[WorkspaceInput(role="source", artifact=SnapshotFile(snapshot_id=row.id, file=file)) for file in files],
        expected_behavior=manifest.request.expected_behavior,
        acceptance_criteria=manifest.request.acceptance_criteria,
    ))


def binding(session: Session) -> tuple[dict[str, Any], Any]:
    root, profile = store_settings()
    if bool(root) == bool(profile):
        raise ForbiddenError("Snapshot storage configuration is unavailable")
    if root:
        if not Path(root).is_absolute():
            raise ForbiddenError("Snapshot storage root must be absolute")
        return {"root": str(Path(root))}, None
    try:
        config, bucket, key = handoff_sources.authorize(session, S3Input(profile_id=int(profile), path="snapshot"))
    except ValueError:
        raise ForbiddenError("Snapshot storage profile is invalid") from None
    suffix = os.getenv("TRITON_PREPARATION_SNAPSHOT_PREFIX", "preparation-snapshots")
    prefix = key.removesuffix("snapshot") + suffix
    return {"endpoint": config["s3_endpoint"], "bucket": bucket, "prefix": prefix.removesuffix("/")}, config


def _authorized_snapshot(
    session: Session, snapshot_id: UUID, snapshot: Snapshot | None = None,
) -> tuple[PreparationSnapshotEntity, SnapshotManifest]:
    row = experiment_snapshots.get(session, snapshot_id)
    manifest = SnapshotManifest.model_validate(row.manifest)
    if snapshot is not None and manifest.source_snapshot != snapshot:
        raise ForbiddenError("Workspace snapshot identity differs from its receipt")
    current, _ = binding(session)
    if row.artifacts["reference"].get("storage_binding") != current:
        raise ForbiddenError("Snapshot storage binding changed or was not recorded; recapture explicitly")
    return row, manifest


def authorize_files(session: Session, artifacts: list[SnapshotFile], snapshot: Snapshot) -> None:
    """Validate one immutable inventory per request; never cache live authorization."""
    if not artifacts:
        return
    snapshot_id = artifacts[0].snapshot_id
    _, manifest = _authorized_snapshot(session, snapshot_id, snapshot)
    files = {file.path: file for file in (*manifest.inventory.files, *manifest.workspace_inventory.files)}
    for artifact in artifacts:
        if artifact.snapshot_id != snapshot_id or files.get(artifact.file.path) != artifact.file:
            raise ForbiddenError("Workspace file is not part of its captured snapshot")


def authorize(session: Session, artifact: SnapshotFile, snapshot: Snapshot | None = None) -> PreparationSnapshotEntity:
    row, manifest = _authorized_snapshot(session, artifact.snapshot_id, snapshot)
    if artifact.file not in (*manifest.inventory.files, *manifest.workspace_inventory.files):
        raise ForbiddenError("Workspace file is not part of its captured snapshot")
    return row


def download(session: Session, artifact: SnapshotFile, destination: Path) -> None:
    row = authorize(session, artifact)
    manifest = SnapshotManifest.model_validate(row.manifest)
    reference = row.artifacts["reference"]
    area = "repository" if artifact.file in manifest.inventory.files else "source-metadata"
    current, config = binding(session)
    if current != reference.get("storage_binding"):
        raise ForbiddenError("Snapshot storage binding changed during download")
    timeout = effective(session).transfer_timeout_seconds
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            if row.artifacts["kind"] == "directory":
                folder = Path(current["root"]) / str(UUID(reference["snapshot_id"])) / area
                fd = _open_directory(folder)
                try:
                    parts = artifact.file.path.split("/")
                    for part in parts[:-1]:
                        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        os.close(fd)
                        fd = child
                    opened = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    stream = stack.enter_context(os.fdopen(opened, "rb"))
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise ConflictError("Snapshot file is no longer a regular retained file")
                finally:
                    os.close(fd)
            else:
                item = next(item for item in reference["objects"]
                            if item["area"] == area and item["path"] == artifact.file.path)
                if (reference["bucket"] != current["bucket"]
                        or not reference["prefix"].startswith(current["prefix"] + "/")
                        or item["key"] != f"{reference['prefix']}/{area}/{artifact.file.path}"
                        or not item["version_id"] or item["version_id"] == "null"):
                    raise ConflictError("Snapshot object reference does not match its storage binding")
                client = build_s3_client(config, client_config=Config(
                    connect_timeout=3, read_timeout=10, retries={"max_attempts": 1},
                ))
                stack.callback(client.close)
                response = client.get_object(Bucket=current["bucket"], Key=item["key"], VersionId=item["version_id"])
                stream = response["Body"]
                stack.callback(stream.close)
                if response.get("VersionId") != item["version_id"]:
                    raise ConflictError("Snapshot object version changed")
            measured, checksum = 0, hashlib.sha256()
            with destination.open("wb") as output:
                while chunk := stream.read(1024**2):
                    measured += len(chunk)
                    if measured > artifact.file.size or time.monotonic() - started > timeout:
                        raise ConflictError("Snapshot file exceeds its captured size or download deadline")
                    checksum.update(chunk)
                    output.write(chunk)
            if measured != artifact.file.size or "sha256:" + checksum.hexdigest() != artifact.file.digest:
                raise ConflictError("Snapshot file differs from its captured inventory")
    except (OSError, BotoCoreError, ClientError, KeyError, StopIteration, ValueError):
        raise ConflictError("Retained snapshot file is unavailable or invalid") from None
