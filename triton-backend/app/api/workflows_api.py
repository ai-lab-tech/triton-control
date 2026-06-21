"""HTTP and WebSocket endpoints for the global Argo Workflows integration."""

from typing import Any

from fastapi import APIRouter, Depends, Request, WebSocket
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.access_control import is_member_or_admin, require_member_or_admin
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    ArgoWorkflowsStatusResponse,
    CreateWorkflowS3CredentialRequest,
    WorkflowS3CredentialDeleteResponse,
    WorkflowS3CredentialDTO,
)
from app.services.workflows import credentials, proxy, status

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("", response_model=ArgoWorkflowsStatusResponse)
@translate_app_errors
def get_argo_workflows_status(
    claims: dict[str, Any] = Depends(get_claims),
) -> ArgoWorkflowsStatusResponse:
    """Return readiness for the global Helm-managed Argo Workflows server."""
    require_member_or_admin(claims)
    return status.get_status()


@router.get("/s3-credentials", response_model=list[WorkflowS3CredentialDTO])
@translate_app_errors
def list_workflow_s3_credentials(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[WorkflowS3CredentialDTO]:
    """List workflow S3 credentials managed by Triton Control."""
    require_member_or_admin(claims)
    return credentials.list_credentials(session)


@router.post("/s3-credentials", response_model=WorkflowS3CredentialDTO)
@translate_app_errors
def create_workflow_s3_credential(
    request: CreateWorkflowS3CredentialRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> WorkflowS3CredentialDTO:
    """Create a workflow S3 credential and mirror it as a Kubernetes secret."""
    require_member_or_admin(claims)
    return credentials.create_credential(request, session, claims)


@router.delete("/s3-credentials/{credential_id}", response_model=WorkflowS3CredentialDeleteResponse)
@translate_app_errors
def delete_workflow_s3_credential(
    credential_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> WorkflowS3CredentialDeleteResponse:
    """Delete a workflow S3 credential and its mirrored Kubernetes secret."""
    require_member_or_admin(claims)
    return credentials.delete_credential(session, credential_id)


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
