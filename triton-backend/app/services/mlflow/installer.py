"""Use cases for singleton MLflow installation and status."""

from __future__ import annotations

from datetime import datetime
from threading import Lock

from sqlmodel import Session

from app.db.entities import MlflowEntity
from app.exceptions import BadRequestError, ConflictError
from app.repositories import mlflow
from app.schemas import (
    InstallMlflowRequest,
    MlflowDeleteResponse,
    MlflowInstallResponse,
    MlflowStatusResponse,
)
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster
from app.services.mlflow import config, kubernetes as k8s

_install_lock = Lock()
_MLFLOW_NAMESPACE = "triton-control"


def get_mlflow_status(session: Session) -> MlflowStatusResponse:
    """Return persisted singleton MLflow installation state."""
    entity = mlflow.get(session)
    if entity is None:
        return MlflowStatusResponse(
            installed=False,
            status="not_installed",
            ready=False,
            status_message="",
            base_path=config.base_path(),
        )

    ready, message = k8s.read_installation_readiness(entity.namespace, entity.deployment_name)
    status = (entity.status or "").strip() or "creating"
    if status == "ready" and not ready:
        status = "creating"
    return MlflowStatusResponse(
        installed=True,
        status=status,
        ready=ready,
        status_message=message or entity.status_message,
        base_path=config.base_path(),
        installation=_to_dto(entity),
    )


def install_mlflow(request: InstallMlflowRequest, session: Session) -> MlflowInstallResponse:
    """Install MLflow resources and return applied Kubernetes resources."""
    if not _install_lock.acquire(blocking=False):
        raise ConflictError("MLflow installation is already in progress")

    try:
        if mlflow.get(session) is not None:
            raise ConflictError("MLflow is already installed")

        name = request.installation_name
        namespace = _MLFLOW_NAMESPACE
        service_name = f"{name}-service"
        entity = mlflow.save(
            session,
            MlflowEntity(
                namespace=namespace,
                deployment_name=name,
                service_name=service_name,
                image=request.image,
                applied_resources=[],
                status="creating",
                status_message="Creating Kubernetes resources and waiting for pod readiness.",
                last_transition_at=datetime.utcnow(),
            ),
        )
        try:
            applied = k8s.apply_installation_resources(
                request,
                namespace=namespace,
                deployment_name=name,
                service_name=service_name,
            )
            entity.applied_resources = applied
            entity.status = "ready"
            entity.status_message = "MLflow pod is Running."
            entity.last_transition_at = datetime.utcnow()
            entity = mlflow.save(session, entity)
            return _to_dto(entity)
        except Exception:
            entity.status = "failed"
            entity.status_message = "MLflow installation failed."
            entity.last_transition_at = datetime.utcnow()
            mlflow.save(session, entity)
            mlflow.delete(session, entity)
            raise
    finally:
        _install_lock.release()


def uninstall_mlflow(session: Session) -> MlflowDeleteResponse:
    """Delete singleton MLflow workload and clear installation record."""
    entity = mlflow.get(session)
    if entity is None:
        raise BadRequestError("MLflow is not installed")

    namespace = entity.namespace
    entity.status = "deleting"
    entity.status_message = "Deleting MLflow resources."
    entity.last_transition_at = datetime.utcnow()
    mlflow.save(session, entity)

    control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
    if namespace == _MLFLOW_NAMESPACE or (control_ns and namespace == control_ns):
        message = k8s.delete_installation_resources(
            namespace=namespace,
            deployment_name=entity.deployment_name,
            service_name=entity.service_name,
        )
    else:
        message = k8s.delete_namespace(namespace)

    mlflow.delete(session, entity)
    return MlflowDeleteResponse(status="deleted", message=message, namespace=namespace)


def get_proxy_server_url(session: Session) -> str:
    """Return internal MLflow server URL for proxying."""
    entity = mlflow.get(session)
    if entity is None:
        raise BadRequestError("MLflow is not installed")
    if (entity.status or "").strip() == "failed":
        raise BadRequestError("MLflow installation is in failed state")
    return k8s.service_url(entity.namespace, entity.service_name)


def _to_dto(entity: MlflowEntity) -> MlflowInstallResponse:
    return MlflowInstallResponse(
        namespace=entity.namespace,
        deployment_name=entity.deployment_name,
        service_name=entity.service_name,
        image=entity.image,
        applied_resources=list(entity.applied_resources or []),
    )
