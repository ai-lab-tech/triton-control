"""Narrow workspace authentication and task scope for the build APIs."""

import os
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.core.identity import claims_from_user
from app.exceptions import ForbiddenError
from app.repositories import task_workspaces, users
from app.services.development.profiles import authenticate_workspace


def development_only() -> None:
    if os.getenv("TRITON_CONTROL_MODE", "development") != "development":
        raise ForbiddenError("Runtime builds are available only in development")


def authenticate(session: Session, workload: str, token: str) -> dict[str, Any]:
    development_only()
    parts = workload.split("/")
    if len(parts) != 2 or not all(parts):
        raise ForbiddenError("A task workspace is required")
    workspace = authenticate_workspace(session, parts[0], parts[1], token)
    association = task_workspaces.for_workspace(session, workspace.id or 0)
    if association is None or association.recovery_reason is not None:
        raise ForbiddenError("Builds require the assigned task workspace")
    owner = users.find_by_id(session, workspace.owner_user_id)
    if owner is None:
        raise ForbiddenError("Workspace owner unavailable")
    claims = claims_from_user(owner, {})
    claims["build_task_id"] = str(association.task_id)
    return claims


def task_scope(claims: dict[str, Any], task_id: UUID | str | None) -> None:
    development_only()
    expected = claims.get("build_task_id")
    if expected is not None and (task_id is None or str(task_id) != expected):
        raise ForbiddenError("Build access is restricted to this workspace task")
