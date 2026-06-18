"""HTTP and WebSocket endpoints for the global Argo Workflows integration."""

from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket

from app.api.errors import translate_app_errors
from app.core.access_control import is_member_or_admin, require_member_or_admin
from app.core.security import get_claims
from app.schemas import ArgoWorkflowsStatusResponse
from app.services.workflows import proxy, status

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("", response_model=ArgoWorkflowsStatusResponse)
@translate_app_errors
def get_argo_workflows_status(
    claims: dict[str, Any] = Depends(get_claims),
) -> ArgoWorkflowsStatusResponse:
    """Return readiness for the global Helm-managed Argo Workflows server."""
    require_member_or_admin(claims)
    return status.get_status()


@router.api_route(
    "/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@router.api_route(
    "/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
@translate_app_errors
async def proxy_argo_workflows(
    request: Request,
    path: str = "",
    claims: dict[str, Any] = Depends(get_claims),
) -> Any:
    """Proxy an authenticated member or admin request to Argo Server."""
    require_member_or_admin(claims)
    return await proxy.proxy_http(path, request)


@router.websocket("/proxy")
@router.websocket("/proxy/{path:path}")
async def proxy_argo_workflows_websocket(
    websocket: WebSocket,
    path: str = "",
) -> None:
    """Proxy an authenticated member or admin WebSocket to Argo Server."""
    claims = websocket.session.get("user")
    if not claims or not is_member_or_admin(claims):
        await websocket.close(code=1008)
        return
    await proxy.proxy_websocket(path, websocket)
