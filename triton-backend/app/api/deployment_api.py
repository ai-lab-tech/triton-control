"""HTTP endpoints for Kubernetes Triton deployments.

Mounts a router at ``/api/deployments`` and exposes:
  ``POST /api/deployments`` - deploy Triton to Kubernetes using an S3 model
                              repository URL and S3 credentials.

Business logic is delegated to ``services.deployment.deployment`` and domain errors are
translated at the HTTP boundary via ``@translate_app_errors``.
"""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    CreateDeploymentRequest,
    DeploymentDeleteResponse,
    DeploymentResponse,
    InstanceLogsResponse,
)
from app.services.deployment import deployment as deployment_service
from app.services.deployment import mlflow as mlflow_service

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


@router.get("/mlflow")
@translate_app_errors
async def list_mlflow_deployments(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[dict[str, Any]]:
    return await mlflow_service.list_all(session, claims)


@router.get("/mlflow/{name}")
@translate_app_errors
async def get_mlflow_deployment(
    name: str,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, Any]:
    return await mlflow_service.get(session, claims, name)


@router.delete("/mlflow/{name}", response_model=DeploymentDeleteResponse)
@translate_app_errors
def delete_mlflow_deployment(
    name: str,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentDeleteResponse:
    return mlflow_service.delete(session, claims, name)


@router.post("", response_model=DeploymentResponse)
@translate_app_errors
def create_deployment(
    request: CreateDeploymentRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentResponse:
    """Deploy Triton on Kubernetes for the provided S3 model repository."""
    return deployment_service.create_deployment(request, session, claims, background_tasks.add_task)


@router.delete("/{instance_id}", response_model=DeploymentDeleteResponse)
@translate_app_errors
def delete_deployment(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentDeleteResponse:
    """Delete a self-deployed Triton instance and its Kubernetes namespace."""
    return deployment_service.delete_deployment_instance(session, claims, instance_id)


@router.get("/{instance_id}/logs", response_model=InstanceLogsResponse)
@translate_app_errors
def get_deployment_logs(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> InstanceLogsResponse:
    """Return deployment and Kubernetes pod logs for an instance."""
    return InstanceLogsResponse(logs=deployment_service.get_deployment_logs(session, claims, instance_id))
