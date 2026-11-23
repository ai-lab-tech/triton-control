"""Task workspace HTTP boundary and narrowly scoped init-container downloads."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse
from sqlmodel import Session
from starlette.background import BackgroundTask

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.task_workspaces import OpenTaskWorkspace, TaskWorkspaceDTO, WorkspaceSizing
from app.services import conversion_exports, task_workspaces

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

@router.post("/conversion-exports/{namespace}/{name}", include_in_schema=False)
@translate_app_errors
def verify_conversion_export(namespace: str, name: str, request: conversion_exports.ConversionExport,
                             authorization: str = Header(default=""),
                             session: Session = Depends(get_session)) -> dict[str, str]:
    scheme, _, token = authorization.partition(" ")
    return conversion_exports.record(session, namespace, name, token if scheme.lower() == "bearer" else "", request)

@router.post("/conversion-exports/{namespace}/{name}/{status}", include_in_schema=False)
@translate_app_errors
def conversion_export_status(namespace: str, name: str, status: Literal["started", "interrupted"],
                             request: conversion_exports.ExportSelection, authorization: str = Header(default=""),
                             session: Session = Depends(get_session)) -> dict[str, str]:
    scheme, _, token = authorization.partition(" ")
    return conversion_exports.export_event(session, namespace, name, token if scheme.lower() == "bearer" else "",
                                          request, "conversion_export_" + status)

@router.get("/conversion-exports/{namespace}/{name}", include_in_schema=False)
@translate_app_errors
def workspace_conversions(namespace: str, name: str, authorization: str = Header(default=""),
                          session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    scheme, _, token = authorization.partition(" ")
    return conversion_exports.saved_conversions(session, namespace, name, token if scheme.lower() == "bearer" else "")

@router.get("/conversion-exports/{namespace}/{name}/{export_id}", include_in_schema=False)
@translate_app_errors
def workspace_conversion_receipt(namespace: str, name: str, export_id: UUID, authorization: str = Header(default=""),
                                 session: Session = Depends(get_session)) -> dict[str, Any]:
    scheme, _, token = authorization.partition(" ")
    return conversion_exports.saved_receipt(session, namespace, name, token if scheme.lower() == "bearer" else "",
                                           export_id)
