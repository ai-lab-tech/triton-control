"""Open a task-scoped workspace with durable association and verified init staging."""

import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlmodel import Session

from app.db.entities import CodeServerEntity, TaskWorkspaceEntity
from app.exceptions import ConflictError, ForbiddenError, NotFoundError, ServiceUnavailableError
from app.repositories import developments
from app.repositories import preparation as tasks
from app.repositories import task_workspaces as repository
from app.schemas.task_workspaces import OpenTaskWorkspace, TaskWorkspaceDTO, WorkspaceInputs
from app.services import handoff_sources
from app.services import task_workspace_access as access
from app.services.development import kubernetes as k8s
from app.services.development import profiles, workspaces
from app.services.registry_settings import apply_registry_settings


def document(task_id: UUID, inputs: WorkspaceInputs) -> dict[str, Any]:
    return {
        "schema_version": 1, "task_id": str(task_id),
        "snapshot": inputs.snapshot.model_dump(mode="json"),
        "expected_behavior": inputs.expected_behavior, "acceptance_criteria": inputs.acceptance_criteria,
        "conversion_directory": "/workspace/conversion",
        "files": [{"path": item.role + "/" + item.artifact.file.path,
                   "size": item.artifact.file.size, "digest": item.artifact.file.digest,
                   "version_id": item.artifact.version_id} for item in inputs.files],
    }


def open_workspace(session: Session, claims: dict[str, Any], task_id: UUID,
                   request: OpenTaskWorkspace) -> TaskWorkspaceDTO:
    workspaces.require_kubernetes_enabled()
    if claims.get("token_use"):
        raise ForbiddenError("Interactive credentials are required to open task workspaces")
    recovery = request.admin_recovery_reason is not None
    try:
        actor = access.authorize(session, claims, task_id, recovery=recovery, lock=True)
        inputs = access.load_inputs(session, task_id)
        access.authorize_inputs(session, inputs)
        workspace = _reserve_workspace(session, task_id, actor, request, inputs)
        # Persist intent before any Kubernetes writes. The same task/name is reused
        # after a crash; no background process-local scheduling state is required.
        session.commit()
        access.authorize(session, claims, task_id, recovery=recovery, lock=True)
        access.authorize_inputs(session, inputs)
        current = repository.get(session, task_id)
        if current is None or current.workspace_id != workspace.id:
            raise ConflictError("Task workspace changed during creation; retry")
        session.refresh(workspace)
        _provision_workspace(session, workspace, request, task_id, inputs)
        session.commit()
        return TaskWorkspaceDTO(task_id=task_id, workspace=workspaces.workspace_dto(workspace))
    except Exception:
        session.rollback()
        raise


def _reserve_workspace(
    session: Session, task_id: UUID, actor: int, request: OpenTaskWorkspace, inputs: WorkspaceInputs,
) -> CodeServerEntity:
    """Reserve identity and audit intent within the caller's task lock."""
    association = repository.get(session, task_id)
    configuration = request.workspace.model_dump(mode="json")
    if association is not None and association.workspace_id is not None:
        workspace = developments.find_by_id(session, association.workspace_id)
        if workspace is None:
            raise ConflictError("Task workspace association requires administrator recovery")
        if workspace.owner_user_id != actor:
            raise ConflictError("Another user owns this task workspace; an administrator must delete it first")
        if (association.configuration != configuration
                or association.recovery_reason != request.admin_recovery_reason
                or association.inputs != inputs.model_dump(mode="json")):
            raise ConflictError("Task workspace already exists with different inputs or settings")
    else:
        name = "task-" + task_id.hex[:24] + "-" + uuid4().hex[:12]
        namespace = workspaces.workspace_namespace()
        workspace = CodeServerEntity(
            owner_user_id=actor, name=name, namespace=namespace, statefulset_name=name,
            service_name=name + "-svc", secret_name=name + "-secret", image=request.workspace.image,
            status="staging", status_message="Task inputs must be verified before the editor starts.",
        )
        repository.save_workspace(session, workspace)
        association = association or TaskWorkspaceEntity(task_id=task_id)
        association.workspace_id = workspace.id
        association.configuration = configuration
        association.inputs = inputs.model_dump(mode="json")
        association.recovery_reason = request.admin_recovery_reason
        repository.save(session, association)
        task = tasks.get(session, task_id)
        task.revision += 1
        tasks.add_event(session, task, actor, "workspace_requested", {
            "workspace_id": workspace.id, "admin_recovery_reason": request.admin_recovery_reason,
        })
    return workspace


def _provision_workspace(
    session: Session, workspace: CodeServerEntity, request: OpenTaskWorkspace,
    task_id: UUID, inputs: WorkspaceInputs,
) -> None:
    """Record complete apply independently of the observed Kubernetes pod status."""
    if not workspace.applied_resources or workspace.status not in {"ready", "creating"}:
        configured = apply_registry_settings(request.workspace, session)
        workspace.applied_resources = []
        try:
            workspace.applied_resources = k8s.apply_code_server_resources(
                configured, namespace=workspace.namespace, statefulset_name=workspace.statefulset_name,
                service_name=workspace.service_name, secret_name=workspace.secret_name,
                task_document=document(task_id, inputs),
            )
        except Exception:
            workspace.status = "blocked"
            workspace.status_message = "Workspace provisioning was interrupted. Reopen the task workspace to retry."
            repository.save_workspace(session, workspace)
            session.commit()
            raise ServiceUnavailableError(workspace.status_message) from None
        workspace.status = "creating"
        workspace.status_message = "Verifying task inputs before starting the editor."
        repository.save_workspace(session, workspace)


def get_workspace(session: Session, claims: dict[str, Any], task_id: UUID) -> TaskWorkspaceDTO:
    association = repository.get(session, task_id)
    if association is None or association.workspace_id is None:
        raise NotFoundError("No workspace has been opened for this task")
    return TaskWorkspaceDTO(task_id=task_id,
                            workspace=workspaces.get_code_server(session, claims, association.workspace_id))


def input_file(session: Session, namespace: str, name: str, token: str, index: int) -> Path:
    workspace = profiles.authenticate_workspace(session, namespace, name, token)
    access.guard(session, workspace)
    association = repository.for_workspace(session, workspace.id or 0)
    if association is None:
        raise ForbiddenError("This workspace has no task inputs")
    inputs = WorkspaceInputs.model_validate(association.inputs)
    if index < 0 or index >= len(inputs.files):
        raise NotFoundError("Task input not found")
    # Download to a private bounded temporary file. Only verified bytes leave the
    # backend; source credentials remain here and are never included in task.json.
    with tempfile.NamedTemporaryFile(prefix="task-input-", delete=False) as handle:
        destination = Path(handle.name)
    try:
        handoff_sources.download(session, inputs.files[index].artifact, destination)
        session.expire_all()
        access.guard(session, workspace)
        return destination
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
