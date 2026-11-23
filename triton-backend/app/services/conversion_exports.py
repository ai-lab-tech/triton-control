"""Verify workspace exports against immutable S3 object versions before recording them."""

import hashlib
import time
from typing import Any
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError
from kubernetes import client  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.entities import CodeServerEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError, ServiceUnavailableError
from app.repositories import conversion_exports as repository
from app.repositories import preparation, s3_profiles, task_workspaces
from app.services.builds.contexts_storage import ContextStore, storage
from app.services.development.profiles import authenticate_workspace
from app.services.kubernetes_client import api_client
from app.services.preparation_settings import effective


class ExportObject(BaseModel):
    key: str = Field(min_length=1, max_length=1024)
    version_id: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=0, le=5 * 1024**3)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class ExportSelection(BaseModel):
    export_id: UUID
    profile_id: int
    directory: str = Field(min_length=1, max_length=1024)


class ConversionExport(ExportSelection):
    objects: list[ExportObject] = Field(min_length=2, max_length=10000)


def verify_objects(store: ContextStore, request: ConversionExport, prefix: str, deadline: float) -> None:
    if request.directory != "." and any(part in {"", ".", ".."} for part in request.directory.split("/")):
        raise BadRequestError("Invalid conversion directory")
    paths = set()
    for obj in request.objects:
        if not obj.key.startswith(prefix + "/") or obj.version_id == "null":
            raise BadRequestError("Expected exact versions within the conversion prefix")
        parts = tuple(obj.key[len(prefix) + 1 :].split("/"))
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise BadRequestError("Invalid model repository path")
        paths.add(parts)
    if len(paths) != len(request.objects):
        raise BadRequestError("Duplicate conversion object keys")
    if any(parts[:depth] in paths for parts in paths for depth in range(1, len(parts))):
        raise BadRequestError("A conversion path cannot be both a file and directory")
    model = "conversion" if request.directory == "." else request.directory.split("/")[-1]
    configs = {parts[:-1] for parts in paths if parts[-1] == "config.pbtxt" and len(parts) in {2, 3}}
    versioned = {
        parts[:depth]
        for depth in (1, 2)
        for parts in paths
        if len(parts) > depth + 1 and parts[depth].isascii() and parts[depth].isdecimal()
    }
    if {parts[0] for parts in paths} != {model} or not configs or not configs.issubset(versioned):
        raise BadRequestError("Expected Triton model configurations with version directories")
    try:
        if store.client.get_bucket_versioning(Bucket=store.bucket).get("Status") != "Enabled":
            raise BadRequestError("Select a versioned S3 bucket")
        for obj in request.objects:
            _verify_object(store, obj, deadline)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "NoSuchVersion", "404", "NotFound"}:
            raise ConflictError("Stored conversion object version is missing") from None
        raise ServiceUnavailableError("Conversion storage unavailable") from None
    except BotoCoreError:
        raise ServiceUnavailableError("Conversion storage unavailable") from None


def _verify_object(store: ContextStore, obj: ExportObject, deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ConflictError("Conversion verification timed out")
    response = store.client.get_object(Bucket=store.bucket, Key=obj.key, VersionId=obj.version_id)
    body = response["Body"]
    try:
        if response["ContentLength"] != obj.size or response.get("VersionId") != obj.version_id:
            raise ConflictError("Stored conversion version or size differs")
        digest = hashlib.sha256()
        count = 0
        for chunk in body.iter_chunks(chunk_size=1024**2):
            count += len(chunk)
            if count > obj.size or time.monotonic() >= deadline:
                raise ConflictError("Conversion verification limit exceeded")
            digest.update(chunk)
        if time.monotonic() >= deadline:
            raise ConflictError("Conversion verification timed out")
        if count != obj.size or digest.hexdigest() != obj.sha256:
            raise ConflictError("Stored conversion checksum differs")
    finally:
        body.close()


def _workspace_task(session: Session, namespace: str, name: str, token: str) -> tuple[CodeServerEntity, UUID]:
    workspace = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason:
        raise ForbiddenError("Select an assigned task workspace")
    return workspace, association.task_id


def _export_identity(selection: ExportSelection, namespace: str, name: str) -> dict[str, Any]:
    return {
        "export_id": str(selection.export_id),
        "directory": selection.directory,
        "profile_id": selection.profile_id,
        "workspace": name,
        "namespace": namespace,
    }


def _check_identity(existing: dict[str, Any], expected: dict[str, Any]) -> None:
    if any(existing.get(key) != value for key, value in expected.items()):
        raise ConflictError("This export identifier is already used")


def export_event(
    session: Session, namespace: str, name: str, token: str, selection: ExportSelection, action: str
) -> dict[str, str]:
    _, task_id = _workspace_task(session, namespace, name, token)
    task = preparation.get(session, task_id, lock=True)
    session.expire_all()
    workspace, current_task_id = _workspace_task(session, namespace, name, token)
    if current_task_id != task_id:
        raise ConflictError("Task workspace changed during export")
    payload = _export_identity(selection, namespace, name)
    previous = repository.latest_event(session, task_id, selection.export_id)
    if previous:
        _check_identity(previous.changes, payload)
        if action == repository.STARTED or previous.action in {action, repository.VERIFIED}:
            return {"status": previous.action.removeprefix("conversion_export_")}
    task.revision += 1
    preparation.add_event(
        session,
        task,
        workspace.owner_user_id,
        action,
        payload,
    )
    session.commit()
    return {"status": action.removeprefix("conversion_export_")}


def record(session: Session, namespace: str, name: str, token: str, request: ConversionExport) -> dict[str, str]:
    _, task_id = _workspace_task(session, namespace, name, token)
    identity = _export_identity(request, namespace, name)
    previous = repository.latest_event(session, task_id, request.export_id)
    if previous:
        _check_identity(previous.changes, identity)
    profile = s3_profiles.get(session, request.profile_id)
    if not profile:
        raise BadRequestError("Unknown S3 profile")
    prefix = "/".join(
        filter(None, [profile.prefix.strip("/"), "task-conversions", str(task_id), str(request.export_id)])
    )
    binding = {
        "profile_id": request.profile_id,
        "endpoint": profile.endpoint,
        "bucket": profile.bucket,
        "prefix": prefix,
    }
    limits = effective(session)
    if sum(obj.size for obj in request.objects) > limits.max_workspace_storage_gib * 1024**3:
        raise BadRequestError("Conversion exceeds the workspace storage limit")
    claim_name = f"workspace-{name}-0"
    claim = client.CoreV1Api(api_client()).read_namespaced_persistent_volume_claim(claim_name, namespace)
    with storage(session, binding) as store:
        if not isinstance(store, ContextStore):
            raise ConflictError("Expected a versioned S3 destination")
        verify_objects(store, request, prefix, time.monotonic() + limits.transfer_timeout_seconds)
    task = preparation.get(session, task_id, lock=True)
    session.expire_all()
    current, current_task_id = _workspace_task(session, namespace, name, token)
    if current_task_id != task_id:
        raise ConflictError("Task workspace changed during export")
    receipt = {**identity, **binding, "claim_name": claim_name, "claim_uid": str(claim.metadata.uid)}
    objects = {obj.key: obj.model_dump() for obj in request.objects}
    previous = repository.latest_event(session, task_id, request.export_id)
    result = {"status": "verified", "export_id": str(request.export_id)}
    if previous:
        _check_identity(previous.changes, identity)
        if previous.action == repository.VERIFIED:
            _check_identity(previous.changes, receipt)
            if {obj["key"]: obj for obj in previous.changes["objects"]} != objects:
                raise ConflictError("This export identifier is already used")
            return result
    task.revision += 1
    preparation.add_event(
        session,
        task,
        current.owner_user_id,
        repository.VERIFIED,
        {**receipt, "objects": list(objects.values())},
    )
    session.commit()
    return result


def saved_conversions(session: Session, namespace: str, name: str, token: str) -> list[dict[str, str | None]]:
    _, task_id = _workspace_task(session, namespace, name, token)
    return [row for row in repository.statuses(session, task_id) if row["status"] == "verified"]


def saved_receipt(session: Session, namespace: str, name: str, token: str, export_id: UUID) -> dict[str, Any]:
    _, task_id = _workspace_task(session, namespace, name, token)
    row = repository.latest_event(session, task_id, export_id, verified_only=True)
    if not row:
        raise NotFoundError("Saved conversion not found on this task")
    return row.changes
