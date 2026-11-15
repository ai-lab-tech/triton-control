"""Authorize instance access before observing its configured workload."""

from typing import Any

from sqlmodel import Session

from app.exceptions import ConflictError
from app.schemas.pods import InstancePodsResponse
from app.services.access import get_instance_or_404
from app.services.deployment.pod_provider import read_owned_state


def get_pods(session: Session, claims: dict[str, Any], instance_id: int) -> InstancePodsResponse:
    instance = get_instance_or_404(session, instance_id, claims)
    response = InstancePodsResponse(
        kubernetes_available=instance.deployment_runtime == "kubernetes",
        health_live=instance.health_live,
        health_ready=instance.health_ready,
    )
    if not response.kubernetes_available:
        return response
    if not instance.deployment_namespace or not instance.deployment_name:
        raise ConflictError("Managed instance has no Kubernetes deployment binding")
    response.deployment, response.pods = read_owned_state(instance.deployment_namespace, instance.deployment_name)
    return response
