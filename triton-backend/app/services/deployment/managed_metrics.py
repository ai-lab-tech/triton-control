"""Resolve metrics destinations from persisted managed identities and live cluster state."""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kubernetes import client  # type: ignore[import-untyped]
from sqlmodel import Session

from app.repositories import deployment_intents
from app.services.deployment.operation_provider import OWNER
from app.services.kubernetes_client import api_client

if TYPE_CHECKING:
    from app.db.entities import TritonInstanceEntity

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class ManagedMetricsService:
    namespace: str
    deployment: str
    service: str

    operation_id: str | None = None
    service_uid: str | None = None
    deployment_uid: str | None = None

    @classmethod
    def from_instance(
        cls, session: Session, instance: "TritonInstanceEntity", requested: str | None
    ) -> "ManagedMetricsService | None":
        if (
            not requested
            or getattr(instance, "is_self_deployed", False) is not True
            or instance.deployment_runtime != "kubernetes"
            or instance.id is None
        ):
            return None
        namespace = instance.deployment_namespace
        deployment = instance.deployment_name
        service = instance.deployment_service_name
        if not namespace or not deployment or not service:
            return None
        if not all(_DNS_LABEL.fullmatch(value) for value in (namespace, deployment, service)):
            return None
        if service != f"{deployment}-service":
            return None
        if not requested.startswith(f"http://{service}.{namespace}.svc.cluster.local:"):
            return None
        intent = deployment_intents.for_instance(session, instance.id)
        if intent is None:
            return cls(namespace, deployment, service)
        if intent.cleaned or intent.delete_requested or (intent.namespace, intent.name) != (namespace, deployment):
            return None
        service_uid = intent.resource_uids.get(f"Service/{service}")
        deployment_uid = intent.resource_uids.get(f"Deployment/{deployment}")
        if not service_uid or not deployment_uid:
            return None
        return cls(namespace, deployment, service, intent.operation_id, service_uid, deployment_uid)

    def verified_url(self, requested: str) -> str:
        with api_client() as api:
            core = client.CoreV1Api(api)
            apps = client.AppsV1Api(api)
            svc = core.read_namespaced_service(self.service, self.namespace, _request_timeout=5)
            dep = apps.read_namespaced_deployment(self.deployment, self.namespace, _request_timeout=5)
            selector = {"app": "triton", "deployment": self.deployment}
            if (
                svc.metadata.deletion_timestamp
                or dep.metadata.deletion_timestamp
                or svc.spec.type != "ClusterIP"
                or not svc.spec.cluster_ip
                or svc.spec.cluster_ip == "None"
                or svc.spec.selector != selector
                or dep.spec.selector.match_labels != selector
                or not selector.items() <= (dep.spec.template.metadata.labels or {}).items()
                or dep.spec.selector.match_expressions
            ):
                raise ValueError("Managed Triton Service does not match its deployment")

            if self.operation_id and (
                svc.metadata.uid != self.service_uid
                or dep.metadata.uid != self.deployment_uid
                or (svc.metadata.labels or {}).get(OWNER) != self.operation_id
                or (dep.metadata.labels or {}).get(OWNER) != self.operation_id
            ):
                raise ValueError("Metrics resources do not match the recorded deployment intent")

            # Operation-managed resources share a ConfigMap anchor. Legacy pairs have no anchor.
            def anchors(metadata: client.V1ObjectMeta) -> set[str]:
                return {ref.uid for ref in metadata.owner_references or [] if ref.kind == "ConfigMap"}

            if anchors(svc.metadata) != anchors(dep.metadata):
                raise ValueError("Managed Triton Service has a different deployment owner")
            container_port = next(
                (
                    p
                    for container in dep.spec.template.spec.containers
                    if container.name == "triton"
                    for p in container.ports or []
                    if p.name == "metrics" and p.container_port == 8002 and p.protocol == "TCP"
                ),
                None,
            )
            if container_port is None:
                raise ValueError("Triton container has no metrics port")
            port = next(
                (
                    p
                    for p in svc.spec.ports
                    if p.name == "metrics"
                    and p.protocol == "TCP"
                    and p.target_port in (container_port.name, container_port.container_port)
                ),
                None,
            )
            if port is None:
                raise ValueError("Managed Triton Service has no metrics port")
            url = f"http://{self.service}.{self.namespace}.svc.cluster.local:{port.port}/metrics"
            if requested != url:
                raise ValueError("Custom metrics endpoint requires TRITON_METRICS_ALLOWED_URLS")

            # Older installations have no shared operation owner reference. Verify the
            # actual routing selector's pods against the deployment's ReplicaSet UIDs.
            query = f"app=triton,deployment={self.deployment}"
            replica_sets = apps.list_namespaced_replica_set(self.namespace, label_selector=query, _request_timeout=5)
            owned = {
                rs.metadata.uid
                for rs in replica_sets.items
                if any(
                    ref.controller and ref.kind == "Deployment" and ref.uid == dep.metadata.uid
                    for ref in rs.metadata.owner_references or []
                )
            }
            pods = core.list_namespaced_pod(self.namespace, label_selector=query, _request_timeout=5)
            if not pods.items or not all(
                any(
                    ref.controller and ref.kind == "ReplicaSet" and ref.uid in owned
                    for ref in pod.metadata.owner_references or []
                )
                for pod in pods.items
            ):
                raise ValueError("Cannot verify pods selected by managed Triton Service")
            return url


async def trusted_metrics_url(identity: ManagedMetricsService | None, requested: str | None) -> str | None:
    if identity is None or not requested:
        return None
    try:
        return await asyncio.to_thread(identity.verified_url, requested)
    except Exception:
        logging.getLogger(__name__).debug(
            "Managed metrics verification failed for %s/%s", identity.namespace, identity.service, exc_info=True
        )
        return None
