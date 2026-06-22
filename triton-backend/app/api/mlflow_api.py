"""HTTP endpoints for singleton MLflow install/status and embedded proxy."""

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.access_control import require_member_or_admin
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    InstallMlflowRequest,
    MlflowDeleteResponse,
    MlflowInstallResponse,
    MlflowStatusResponse,
)
from app.services.mlflow import installer, proxy

router = APIRouter(prefix="/api/mlflow", tags=["mlflow"])


@router.get("", response_model=MlflowStatusResponse)
@translate_app_errors
def get_mlflow_status(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> MlflowStatusResponse:
    """Return singleton MLflow status for member/admin users."""
    require_member_or_admin(claims)
    return installer.get_mlflow_status(session)


@router.post("", response_model=MlflowInstallResponse)
@translate_app_errors
def install_mlflow(
    request: InstallMlflowRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> MlflowInstallResponse:
    """Install singleton MLflow on Kubernetes."""
    require_member_or_admin(claims)
    return installer.install_mlflow(request, session)


@router.delete("", response_model=MlflowDeleteResponse)
@translate_app_errors
def uninstall_mlflow(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> MlflowDeleteResponse:
    """Uninstall singleton MLflow from Kubernetes."""
    require_member_or_admin(claims)
    return installer.uninstall_mlflow(session)


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
async def proxy_mlflow(
    request: Request,
    path: str = "",
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    """Proxy authenticated request to embedded singleton MLflow server."""
    require_member_or_admin(claims)
    return await proxy.proxy_http(path, request, session)
