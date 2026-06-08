"""Use cases for self-managed Triton deployments on Kubernetes.

Public surface: ``create_deployment``, ``delete_deployment_instance``, and
``get_deployment_logs``. These functions coordinate access checks, database
records, and Kubernetes deployment tasks.

Service layer only; raises domain errors and contains no FastAPI route handlers.
"""

from __future__ import annotations

from typing import Any, Callable

from sqlmodel import Session

from app.exceptions import BadRequestError
from app.schemas import CreateDeploymentRequest, DeploymentDeleteResponse, DeploymentResponse
from app.services.access import get_instance_or_404
from app.services.deployment import kubernetes as k8s
from app.services.deployment.records import delete_instance_record, upsert_deployed_instance
from app.services.kubernetes_client import in_cluster_namespace, is_running_in_cluster


def create_deployment(
    request: CreateDeploymentRequest,
    session: Session,
    claims: dict[str, Any],
    schedule_task: Callable[..., None] | None = None,
) -> DeploymentResponse:
    name = request.deployment_name
    control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
    ns = control_ns or name
    deploy = name
    svc = f"{name}-service"
    secret = f"{name}-s3-credentials"
    image = request.image
    triton_url = k8s.pending_url(ns, svc, request.ingress_host, request.ingress_scheme)
    metrics_url = (
        k8s.pending_metrics_url(ns, svc, request.ingress_host, request.ingress_scheme)
        if request.allow_metrics
        else None
    )

    instance = upsert_deployed_instance(
        request, session, claims,
        namespace=ns, deployment_name=deploy, service_name=svc, secret_name=secret,
        image=image, triton_url=triton_url, metrics_url=metrics_url, initial_snapshot=k8s.pending_snapshot(),
    )
    args = (instance.id or 0, request, ns, deploy, svc, secret, image)
    if schedule_task:
        schedule_task(k8s.apply_deployment_resources, *args)
    else:
        k8s.apply_deployment_resources(*args)

    applied_resources = [
        f"Secret/{secret}",
        f"Deployment/{deploy}",
        f"Service/{svc}",
    ]
    if (request.ingress_host or "").strip():
        applied_resources.append(f"Ingress/{deploy}-ingress")
    if request.dockerconfigjson:
        applied_resources.insert(0, "Secret/artifactory-token")

    return DeploymentResponse(
        instance_id=instance.id or 0,
        namespace=ns,
        deployment_name=deploy,
        service_name=svc,
        secret_name=secret,
        image=image,
        s3_url=request.s3_url,
        applied_resources=applied_resources,
    )


def delete_deployment_instance(session: Session, claims: dict[str, Any], instance_id: int) -> DeploymentDeleteResponse:
    instance = get_instance_or_404(session, instance_id, claims)
    if not instance.is_self_deployed:
        raise BadRequestError("Instance is not managed by Kubernetes deployment")
    namespace = (instance.deployment_namespace or "").strip()
    message = "No Kubernetes namespace was recorded for this instance."
    if namespace:
        try:
            control_ns = in_cluster_namespace() if is_running_in_cluster() else ""
            if control_ns and namespace == control_ns:
                message = k8s.delete_deployment_resources(
                    namespace=namespace,
                    deployment_name=(instance.deployment_name or "").strip(),
                    service_name=(instance.deployment_service_name or "").strip(),
                    secret_name=(instance.deployment_secret_name or "").strip(),
                )
            else:
                message = k8s.delete_namespace(namespace)
        except Exception as exc:
            raise BadRequestError("Kubernetes configuration could not be loaded") from exc
    delete_instance_record(session, instance)
    return DeploymentDeleteResponse(status="deleted", message=message, namespace=namespace)


def get_deployment_logs(session: Session, claims: dict[str, Any], instance_id: int) -> str:
    instance = get_instance_or_404(session, instance_id, claims)
    namespace = (instance.deployment_namespace or "").strip()
    if not instance.is_self_deployed or not namespace:
        return instance.deployment_log or ""
    try:
        live = k8s.read_deployment_logs(namespace, instance.deployment_name)
    except Exception as exc:
        raise BadRequestError("Kubernetes configuration could not be loaded") from exc
    stored = (instance.deployment_log or "").strip()
    return "\n\n".join(part for part in [stored, live] if part)
