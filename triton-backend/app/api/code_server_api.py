"""HTTP endpoints for per-user Kubernetes code-server workspaces."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response, WebSocket
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session, session_factory
from app.exceptions import AppError
from app.schemas import CodeServerDeleteResponse, CodeServerDTO, CreateCodeServerRequest
from app.services.code_server import proxy as code_server_proxy
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


@router.api_route(
    "/{code_server_id}/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@router.api_route(
    "/{code_server_id}/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@translate_app_errors
async def proxy_code_server(
    code_server_id: int,
    request: Request,
    path: str = "",
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    """Proxy an authenticated request to an owned code-server workspace."""
    row = workspaces.get_owned_code_server(session, claims, code_server_id)
    return await code_server_proxy.proxy_http(row, path, request)


@router.websocket("/{code_server_id}/proxy")
@router.websocket("/{code_server_id}/proxy/{path:path}")
async def proxy_code_server_websocket(
    websocket: WebSocket,
    code_server_id: int,
    path: str = "",
) -> None:
    """Proxy an authenticated code-server WebSocket to an owned workspace."""
    claims = websocket.session.get("user")
    if not claims:
        await websocket.close(code=1008)
        return
    try:
        with session_factory() as session:
            row = workspaces.get_owned_code_server(session, claims, code_server_id)
            await code_server_proxy.proxy_websocket(row, path, websocket)
    except AppError:
        await websocket.close(code=1008)
