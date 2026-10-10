"""HTTP and WebSocket endpoints for the global Argo Workflows integration."""

from typing import Any

import anyio
from fastapi import APIRouter, Depends, Request, WebSocket
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.access_control import is_member_or_admin, require_admin, require_member_or_admin
from app.core.identity import require_user_entity
from app.core.security import get_claims
from app.db.database import get_session
from app.exceptions import UnauthorizedError
from app.repositories import s3_profiles
from app.schemas import (
    ArgoWorkflowsStatusResponse,
    CreateWorkflowS3CredentialRequest,
    WorkflowS3CredentialDeleteResponse,
    WorkflowS3CredentialDTO,
)
from app.schemas.workflows import WorkflowS3ProfileChoice, WorkspaceArgoUpgradeResponse
from app.services.workflows import credentials, proxy, status, workspace_access

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("", response_model=ArgoWorkflowsStatusResponse)
@translate_app_errors
def get_argo_workflows_status(
    claims: dict[str, Any] = Depends(get_claims),
) -> ArgoWorkflowsStatusResponse:
    """Return readiness for the global Helm-managed Argo Workflows server."""
    require_member_or_admin(claims)
    return status.get_status()


@router.get("/s3-profile-choices", response_model=list[WorkflowS3ProfileChoice])
@translate_app_errors
def list_s3_profile_choices(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[WorkflowS3ProfileChoice]:
    require_member_or_admin(claims)
    owner = require_user_entity(session, claims)
    return [WorkflowS3ProfileChoice(
        id=p.id, name=p.name, endpoint=p.endpoint, bucket=p.bucket, region=p.region,
    ) for p in s3_profiles.list_for_owner(session, owner.id or 0)]


@router.post("/s3-credentials/{credential_id}/sync", response_model=WorkflowS3CredentialDTO)
@translate_app_errors
def sync_workflow_s3_credential(
    credential_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> WorkflowS3CredentialDTO:
    require_member_or_admin(claims)
    return credentials.retry_sync(session, claims, credential_id)


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


@router.post("/upgrade-workspaces", response_model=WorkspaceArgoUpgradeResponse)
@translate_app_errors
def upgrade_workspace_argo_access(claims: dict[str, Any] = Depends(get_claims)) -> WorkspaceArgoUpgradeResponse:
    """Migrate existing workspace credentials and environment without deleting data."""
    require_admin(claims)
    count = workspace_access.upgrade_workspaces()
    return WorkspaceArgoUpgradeResponse(
        upgraded_workspaces=count, message="Managed Argo access enabled; workspace pods roll to load the environment.",
    )


@router.api_route(
    "/workspaces/{namespace}/{workspace_name}/workflows", methods=["GET", "POST"], include_in_schema=False,
)
@router.api_route(
    "/workspaces/{namespace}/{workspace_name}/workflows/{workflow_name}",
    methods=["GET", "DELETE"], include_in_schema=False,
)
@router.api_route(
    "/workspaces/{namespace}/{workspace_name}/workflows/{workflow_name}/{action}",
    methods=["PUT"], include_in_schema=False,
)
@translate_app_errors
async def proxy_workspace_workflows(
    request: Request, namespace: str, workspace_name: str,
) -> Any:
    """Scope workspace credentials to validated submissions and their owner's workflows."""
    workflow_name = request.path_params.get("workflow_name", "")
    action = request.path_params.get("action", "")
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        raise UnauthorizedError("Argo workspace authentication required")
    claims = await anyio.to_thread.run_sync(workspace_access.authenticate_workspace, namespace, workspace_name, token)
    path = await anyio.to_thread.run_sync(workspace_access.workflow_path, workflow_name, action, claims)
    listing = not workflow_name and request.method == "GET"
    if listing:
        request = workspace_access.owner_filtered_request(request, claims)
    response = await proxy.proxy_http(path, request, claims)
    return await workspace_access.filter_list_response(response, claims) if listing else response


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
    return await proxy.proxy_http(path, request, claims)


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
