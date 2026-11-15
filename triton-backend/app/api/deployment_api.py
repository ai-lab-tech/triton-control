"""HTTP endpoints for Kubernetes Triton deployments.

Mounts a router at ``/api/deployments`` and exposes:
  ``POST /api/deployments`` - deploy Triton to Kubernetes using an S3 model
                              repository URL and S3 credentials.

Business logic is delegated to ``services.deployment.deployment`` and domain errors are
translated at the HTTP boundary via ``@translate_app_errors``.
"""

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
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
from app.schemas.deployment_access import DeploymentAccessRequest, DeploymentAccessResponse
from app.schemas.deployment_policy import DeploymentPolicyResponse, DeploymentPolicyUpdate
from app.schemas.operations import OperationResponse
from app.services.deployment import access as access_service
from app.services.deployment import deployment as deployment_service
from app.services.deployment import mlflow as mlflow_service
from app.services.deployment import operations as deployment_operations
from app.services.deployment import policy as policy_service

router = APIRouter(prefix="/api/deployments", tags=["deployments"])


@router.get("/policy", response_model=DeploymentPolicyResponse)
@translate_app_errors
def get_deployment_policy(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentPolicyResponse:
    return policy_service.get_policy(session)


@router.put("/policy", response_model=DeploymentPolicyResponse)
@translate_app_errors
def update_deployment_policy(
    request: DeploymentPolicyUpdate,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentPolicyResponse:
    return policy_service.update_policy(request, session, claims)


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


@router.post("", response_model=DeploymentResponse, status_code=202)
@translate_app_errors
def create_deployment(
    request: CreateDeploymentRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, min_length=1, max_length=128),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentResponse:
    """Durably accept creation; readiness is reported by the linked operation."""
    result = deployment_operations.submit(request, session, claims, idempotency_key)
    response.headers["Location"] = result.status_url or ""
    return result


@router.get("/requests/{key}", response_model=DeploymentResponse | None)
@translate_app_errors
def find_deployment_request(
    key: str, deployment_name: str = Query(min_length=1),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentResponse | None:
    return deployment_operations.find_request(session, claims, key, deployment_name)


@router.get("/{instance_id}/operation", response_model=OperationResponse | None)
@translate_app_errors
def deployment_operation(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse | None:
    return deployment_operations.status(session, claims, instance_id)


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


@router.get("/{instance_id}/access", response_model=DeploymentAccessResponse)
@translate_app_errors
def get_deployment_access(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentAccessResponse:
    return access_service.get_access(session, claims, instance_id)


@router.put("/{instance_id}/access", response_model=DeploymentAccessResponse)
@translate_app_errors
def update_deployment_access(
    instance_id: int,
    request: DeploymentAccessRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> DeploymentAccessResponse:
    return access_service.update_access(request, session, claims, instance_id)
