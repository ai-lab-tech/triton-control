"""Verify workspace exports against immutable S3 object versions before recording them."""

import hashlib
import time
from typing import Any
from uuid import UUID

from kubernetes import client  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app.db.entities import PreparationTaskEventEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.repositories import engineering_queue, preparation, s3_profiles, task_workspaces
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
    keys = {obj.key for obj in request.objects}
    if len(keys) != len(request.objects):
        raise BadRequestError("Duplicate conversion object keys")
    paths = set()
    for obj in request.objects:
        if not obj.key.startswith(prefix + "/") or obj.version_id == "null":
            raise BadRequestError("Expected exact versions within the conversion prefix")
        parts = tuple(obj.key[len(prefix) + 1 :].split("/"))
        if len(parts) < 2 or any(part in {"", ".", ".."} for part in parts):
            raise BadRequestError("Invalid model repository path")
        paths.add(parts)
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
    if store.client.get_bucket_versioning(Bucket=store.bucket).get("Status") != "Enabled":
        raise BadRequestError("Select a versioned S3 bucket")
    for obj in request.objects:
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
            if count != obj.size or digest.hexdigest() != obj.sha256:
                raise ConflictError("Stored conversion checksum differs")
        finally:
            body.close()


def export_event(
    session: Session, namespace: str, name: str, token: str, selection: ExportSelection, action: str
) -> dict[str, str]:
    workspace = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason:
        raise ForbiddenError("Select an assigned task workspace")
    task = preparation.get(session, association.task_id, lock=True)
    payload = selection.model_dump(mode="json")
    previous = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.task_id == task.id,
            col(PreparationTaskEventEntity.changes)["export_id"].as_string() == str(selection.export_id),
        )
    ).all()
    for event in previous:
        if (
            event.changes.get("workspace") != name
            or event.changes.get("directory") != selection.directory
            or event.changes.get("profile_id") != selection.profile_id
        ):
            raise ConflictError("This export identifier is already used")
        if event.action in {action, "conversion_export_verified"}:
            return {"status": event.action.removeprefix("conversion_export_")}
    task.revision += 1
    preparation.add_event(
        session,
        task,
        workspace.owner_user_id,
        action,
        {
            **payload,
            "workspace": name,
            "namespace": namespace,
        },
    )
    session.commit()
    return {"status": action.removeprefix("conversion_export_")}


def record(session: Session, namespace: str, name: str, token: str, request: ConversionExport) -> dict[str, str]:
    workspace = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason:
        raise ForbiddenError("Select an assigned task workspace")
    task_id = association.task_id
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
    current = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, current.id or 0)
    if association is None or association.task_id != task_id or association.recovery_reason:
        raise ConflictError("Task workspace changed during export")
    existing = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.task_id == task.id,
            PreparationTaskEventEntity.action == "conversion_export_verified",
            col(PreparationTaskEventEntity.changes)["export_id"].as_string() == str(request.export_id),
        )
    ).first()
    if existing:
        if (
            existing.changes["objects"] != [obj.model_dump() for obj in request.objects]
            or existing.changes["claim_uid"] != str(claim.metadata.uid)
            or existing.changes["profile_id"] != request.profile_id
            or existing.changes["directory"] != request.directory
        ):
            raise ConflictError("This export identifier is already used")
        return {"status": "verified", "export_id": str(request.export_id)}
    task.revision += 1
    preparation.add_event(
        session,
        task,
        current.owner_user_id,
        "conversion_export_verified",
        {
            **binding,
            "directory": request.directory,
            "export_id": str(request.export_id),
            "namespace": namespace,
            "workspace": name,
            "claim_name": claim_name,
            "claim_uid": str(claim.metadata.uid),
            "objects": [obj.model_dump() for obj in request.objects],
        },
    )
    session.commit()
    return {"status": "verified", "export_id": str(request.export_id)}


def saved_conversions(session: Session, namespace: str, name: str, token: str) -> list[dict[str, str | None]]:
    workspace = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason:
        raise ForbiddenError("Select an assigned task workspace")
    return [
        row
        for row in engineering_queue.conversion_statuses(session, association.task_id)
        if row["status"] == "verified"
    ]


def saved_receipt(session: Session, namespace: str, name: str, token: str, export_id: UUID) -> dict[str, Any]:
    workspace = authenticate_workspace(session, namespace, name, token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason:
        raise ForbiddenError("Select an assigned task workspace")
    row = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.task_id == association.task_id,
            PreparationTaskEventEntity.action == "conversion_export_verified",
            col(PreparationTaskEventEntity.changes)["export_id"].as_string() == str(export_id),
        )
    ).first()
    if not row:
        raise NotFoundError("Saved conversion not found on this task")
    return row.changes
