"""Current task and source authorization for browser and workload access."""

from typing import Any
from uuid import UUID

from pydantic import TypeAdapter
from sqlmodel import Session

from app.core.identity import claims_from_user
from app.db.entities import CodeServerEntity
from app.exceptions import ConflictError, ForbiddenError
from app.repositories import experiment_snapshots, handoffs, task_workspaces, users
from app.repositories import preparation as tasks
from app.schemas.handoffs import ResolvedInput, ResolvedRepository
from app.schemas.lineage import InventoryV1
from app.schemas.task_workspaces import SnapshotFile, WorkspaceInput, WorkspaceInputs
from app.services import handoff_sources, preparation
from app.services.lineage import inventory_digest
from app.services.preparation_settings import effective


def authorize(session: Session, claims: dict[str, Any], task_id: UUID, *, recovery: bool = False,
              lock: bool = False) -> int:
    actor, admin, eligible = preparation.authorize_task_access(session, claims, task_id, lock=lock)
    task = tasks.get(session, task_id)
    if task.state == "cancelled":
        raise ConflictError("Cancelled tasks cannot open a workspace")
    if recovery:
        if not admin:
            raise ForbiddenError("Admin recovery requires a current administrator")
    elif not (eligible and task.assignee_user_id == actor):
        raise ForbiddenError("Only the current eligible assignee may open this task workspace")
    snapshot = experiment_snapshots.find(session, task.source_snapshot_id) if task.source_snapshot_id else None
    if preparation.get_task(session, claims, task_id).has_blocking_inputs and snapshot is None:
        raise ConflictError("Resolve the outstanding task inputs before opening a workspace")
    return actor


def load_inputs(session: Session, task_id: UUID) -> WorkspaceInputs:
    task = tasks.get(session, task_id)
    if task.source_snapshot_id is not None:
        snapshot = task_workspaces.snapshot_inputs(session, task.source_snapshot_id)
        if snapshot is not None:
            return snapshot
    receipts = handoffs.for_tasks(session, [task_id])
    if not receipts:
        raise ConflictError("This snapshot has no registered workspace inputs")
    payload = receipts[0].payload
    source: ResolvedInput | ResolvedRepository = TypeAdapter(ResolvedInput | ResolvedRepository).validate_python(
        payload["source"],
    )
    source_files = source.files if isinstance(source, ResolvedRepository) else [source]
    return WorkspaceInputs(
        snapshot=payload["source_snapshot"],
        files=[WorkspaceInput(role="source", artifact=item) for item in source_files] + [
            WorkspaceInput.model_validate({"role": role, "artifact": payload[role]})
            for role in ("dataset", "baseline") if payload[role] is not None
        ],
        expected_behavior=payload["request"]["expected_behavior"],
        acceptance_criteria=payload["request"]["acceptance_criteria"],
    )


def authorize_inputs(session: Session, inputs: WorkspaceInputs) -> None:
    source_inventory = InventoryV1(files=tuple(item.artifact.file for item in inputs.files if item.role == "source"))
    if inventory_digest(source_inventory) != inputs.snapshot.inventory:
        raise ConflictError("Workspace source inventory does not match the captured snapshot")
    if sum(item.artifact.file.size for item in inputs.files) > effective(session).max_input_gib * 1024**3:
        raise ConflictError("Workspace inputs exceed the configured staging limit")
    if len(inputs.model_dump_json().encode()) > 512 * 1024:
        raise ConflictError("Workspace input manifest exceeds the 512 KiB limit")
    snapshots = [item.artifact for item in inputs.files if isinstance(item.artifact, SnapshotFile)]
    if snapshots:
        from app.services.snapshots.inputs import authorize_files
        authorize_files(session, snapshots, inputs.snapshot)
    # S3 profiles currently grant installation-wide member/admin access. Never
    # derive an artifact permission from team membership, nor accept an arbitrary URL.
    for item in inputs.files:
        artifact = item.artifact
        if isinstance(artifact, SnapshotFile):
            continue
        config, bucket, key = handoff_sources.authorize(session, artifact.source)
        if (config["s3_endpoint"], bucket, key) != (artifact.endpoint, artifact.bucket, artifact.key):
            raise ForbiddenError("Captured source is no longer available through its authorized profile")


def guard(session: Session, workspace: CodeServerEntity) -> UUID | None:
    """Return the task identity after verifying current workspace and source access."""
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None:
        return None
    owner = users.find_by_id(session, workspace.owner_user_id)
    if owner is None or not owner.is_active:
        raise ForbiddenError("Task workspace owner is inactive")
    authorize(session, claims_from_user(owner, {}), association.task_id,
              recovery=association.recovery_reason is not None)
    authorize_inputs(session, WorkspaceInputs.model_validate(association.inputs))
    return association.task_id
