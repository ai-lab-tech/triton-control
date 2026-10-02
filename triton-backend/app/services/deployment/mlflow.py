"""Owner-scoped MLflow deployment lookup and live model readiness."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.identity import require_user_entity
from app.exceptions import NotFoundError
from app.repositories import instances
from app.schemas import DeploymentDeleteResponse
from app.services.deployment import deployment
from app.services.triton.client import TritonService


def _owned(session: Session, claims: dict[str, Any], name: str) -> Any:
    if claims.get("token_use") == "mlflow_workflow" and name != claims.get("allowed_deployment_name"):
        raise NotFoundError("MLflow deployment not found")
    owner = require_user_entity(session, claims)
    row = instances.find_by_name(session, name)
    if not row or row.created_by_user_id != owner.id or not row.mlflow_model_uri:
        raise NotFoundError("MLflow deployment not found")
    return row


async def describe(row: Any) -> dict[str, Any]:
    state = "starting"
    detail = row.health_error or "Waiting for Triton model readiness"
    if row.url and row.mlflow_model_name:
        service = TritonService(row.url, row.triton_verify_ssl, row.triton_ca_certificate, timeout=5.0)
        if await service.is_ready():
            entries = await service.get_repository_index()
            for entry in entries:
                if entry.get("name") == row.mlflow_model_name and entry.get("state") == "READY":
                    state, detail = "ready", "Model is ready"
                    break
            else:
                detail = "Model is not ready"
    return {
        "name": row.name,
        "instance_id": row.id,
        "model_uri": row.mlflow_model_uri,
        "model_name": row.mlflow_model_name,
        "status": state,
        "status_detail": detail,
        "url": row.url,
    }


async def get(session: Session, claims: dict[str, Any], name: str) -> dict[str, Any]:
    return await describe(_owned(session, claims, name))


async def list_all(session: Session, claims: dict[str, Any]) -> list[dict[str, Any]]:
    owner = require_user_entity(session, claims)
    rows = instances.list_mlflow_for_owner(session, owner.id or 0)
    if claims.get("token_use") == "mlflow_workflow":
        rows = [row for row in rows if row.name == claims.get("allowed_deployment_name")]
    return [await describe(row) for row in rows]


def delete(session: Session, claims: dict[str, Any], name: str) -> DeploymentDeleteResponse:
    row = _owned(session, claims, name)
    return deployment.delete_deployment_instance(session, claims, row.id)
