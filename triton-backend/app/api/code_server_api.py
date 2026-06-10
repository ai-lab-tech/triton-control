"""HTTP endpoints for per-user Kubernetes code-server workspaces."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import CodeServerDeleteResponse, CodeServerDTO, CreateCodeServerRequest
from app.services.code_server import workspaces

router = APIRouter(prefix="/api/code-servers", tags=["code-servers"])


@router.get("", response_model=list[CodeServerDTO])
@translate_app_errors
def list_code_servers(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[CodeServerDTO]:
    """Return code-server workspaces owned by the authenticated user."""
    return workspaces.list_code_servers(session, claims)


@router.post("", response_model=CodeServerDTO)
@translate_app_errors
def create_code_server(
    request: CreateCodeServerRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> CodeServerDTO:
    """Create or replace the caller's code-server workspace."""
    return workspaces.create_code_server(request, session, claims)


@router.get("/{code_server_id}", response_model=CodeServerDTO)
@translate_app_errors
def get_code_server(
    code_server_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> CodeServerDTO:
    """Return a single code-server workspace owned by the authenticated user."""
    return workspaces.get_code_server(session, claims, code_server_id)


@router.delete("/{code_server_id}", response_model=CodeServerDeleteResponse)
@translate_app_errors
def delete_code_server(
    code_server_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> CodeServerDeleteResponse:
    """Delete a code-server workload owned by the authenticated user."""
    return workspaces.delete_code_server(session, claims, code_server_id)
