"""Task workspace HTTP boundary and narrowly scoped init-container downloads."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from sqlmodel import Session
from starlette.background import BackgroundTask

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.task_workspaces import OpenTaskWorkspace, TaskWorkspaceDTO, WorkspaceSizing
from app.services import task_workspaces

router = APIRouter(prefix="/api/preparation", tags=["task-workspaces"])


@router.post("/tasks/{task_id}/workspace", response_model=TaskWorkspaceDTO)
@translate_app_errors
def open_workspace(task_id: UUID, request: OpenTaskWorkspace, session: Session = Depends(get_session),
                   claims: dict[str, Any] = Depends(get_claims)) -> TaskWorkspaceDTO:
    return task_workspaces.open_workspace(session, claims, task_id, request)


@router.get("/tasks/{task_id}/workspace", response_model=TaskWorkspaceDTO)
@translate_app_errors
def get_workspace(task_id: UUID, session: Session = Depends(get_session),
                  claims: dict[str, Any] = Depends(get_claims)) -> TaskWorkspaceDTO:
    return task_workspaces.get_workspace(session, claims, task_id)


@router.get("/workspace-inputs/{namespace}/{name}/{index}", include_in_schema=False)
@translate_app_errors
def input_file(namespace: str, name: str, index: int, authorization: str = Header(default=""),
               session: Session = Depends(get_session)) -> FileResponse:
    scheme, _, token = authorization.partition(" ")
    path = task_workspaces.input_file(session, namespace, name, token if scheme.lower() == "bearer" else "", index)
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "no-store"},
                        background=BackgroundTask(path.unlink, missing_ok=True))


@router.get("/tasks/{task_id}/workspace-sizing", response_model=WorkspaceSizing)
@translate_app_errors
def workspace_sizing(task_id: UUID, session: Session = Depends(get_session),
                     claims: dict[str, Any] = Depends(get_claims)) -> WorkspaceSizing:
    return task_workspaces.workspace_sizing(session, claims, task_id)
