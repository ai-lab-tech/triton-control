"""Persistence for task workspace associations and immutable snapshot inputs."""

from uuid import UUID

from sqlmodel import Session, select

from app.db.entities import CodeServerEntity, TaskWorkspaceEntity, WorkspaceSnapshotInputsEntity
from app.exceptions import ConflictError
from app.schemas.task_workspaces import WorkspaceInputs


def get(session: Session, task_id: UUID) -> TaskWorkspaceEntity | None:
    return session.get(TaskWorkspaceEntity, task_id, populate_existing=True)


def for_workspace(session: Session, workspace_id: int) -> TaskWorkspaceEntity | None:
    return session.exec(select(TaskWorkspaceEntity).where(TaskWorkspaceEntity.workspace_id == workspace_id)).first()


def save(session: Session, row: TaskWorkspaceEntity) -> None:
    session.add(row)
    session.flush()


def snapshot_inputs(session: Session, snapshot_id: UUID) -> WorkspaceInputs | None:
    row = session.get(WorkspaceSnapshotInputsEntity, snapshot_id)
    return WorkspaceInputs.model_validate(row.payload) if row else None


def register_snapshot_inputs(session: Session, snapshot_id: UUID, inputs: WorkspaceInputs) -> None:
    """Internal capture hook: caller authorizes sources; joins its transaction."""
    row = session.get(WorkspaceSnapshotInputsEntity, snapshot_id)
    payload = inputs.model_dump(mode="json")
    if row is not None:
        if row.payload != payload:
            raise ConflictError("Snapshot inputs are immutable; capture a new revision")
        return
    session.add(WorkspaceSnapshotInputsEntity(snapshot_id=snapshot_id, payload=payload))
    session.flush()


def save_workspace(session: Session, workspace: CodeServerEntity) -> None:
    """Join the caller's intent transaction without committing prematurely."""
    session.add(workspace)
    session.flush()
