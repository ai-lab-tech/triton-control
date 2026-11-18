"""Read actual owned workload evidence; never resolve a mutable image tag."""

import re
from typing import Any

from app.exceptions import ConflictError
from app.schemas.experiment_snapshots import RuntimeImage, RuntimeObservation
from app.services.deployment.pod_provider import PodProvider
from app.services.kubernetes_client import api_client


class RuntimeProvider:
    def observe(self, namespace: str, name: str) -> RuntimeObservation:
        with api_client() as api:
            return self.read(PodProvider(api), namespace, name)

    def read(self, provider: PodProvider, namespace: str, name: str) -> RuntimeObservation:
        before = provider.deployment(namespace, name)
        state, pods = provider.snapshot(namespace, name)
        after = provider.deployment(namespace, name)
        if before["metadata"] != after["metadata"] or state.stale:
            raise ConflictError("Deployment changed or has an unobserved generation")
        if (
            state.updated < state.desired
            or state.ready < state.desired
            or state.available < state.desired
            or any(not pod.terminating and not pod.current_revision for pod in pods)
        ):
            raise ConflictError("Capture requires a stable current rollout")
        images = []
        for pod in pods:
            if pod.terminating or not pod.current_revision:
                continue
            for container in pod.containers:
                if container.name == "triton":
                    platform = None
                    if pod.node:
                        try:
                            node = provider.data(provider.core.read_node(pod.node, _request_timeout=(3, 5)))
                            info = node.get("status", {}).get("nodeInfo", {})
                            architecture, system = info.get("architecture"), info.get("operatingSystem")
                            if architecture in {"amd64", "arm64"} and system in {"linux", "windows"}:
                                platform = f"{system}/{architecture}"
                        except Exception:
                            platform = None  # Missing node permission becomes an explicit platform gap.
                    images.append(
                        RuntimeImage(
                            declared=safe_image(container.image),
                            observed=actual_image(container.image_id),
                            node=pod.node,
                            pod_uid=pod.uid,
                            restart_count=container.restart_count,
                            platform=platform,
                        )
                    )
        spec = before.get("spec", {}).get("template", {}).get("spec", {})
        triton: dict[str, Any] = next((c for c in spec.get("containers", []) if c.get("name") == "triton"), {})
        # No environment, commands, annotations, S3 URLs, Secrets or raw Pod specs.
        resources: dict[str, dict[str, str]] = {}
        for group in ("requests", "limits"):
            values = triton.get("resources", {}).get(group, {})
            resources[group] = {
                key: str(value)
                for key, value in values.items()
                if key in {"cpu", "memory", "nvidia.com/gpu"}
                and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?[a-zA-Z]*", str(value))
            }
        return RuntimeObservation(
            managed=True,
            deployment_uid=state.uid,
            generation=state.generation,
            observed_generation=state.observed_generation,
            resource_version=before["metadata"].get("resourceVersion"),
            images=tuple(sorted(images, key=lambda image: image.pod_uid)),
            resources=resources,
        )


def safe_image(value: str | None) -> str | None:
    return value if value and len(value) <= 512 and re.fullmatch(r"[a-zA-Z0-9./_:@-]+", value) else None


def actual_image(value: str | None) -> str | None:
    cleaned = (value or "").removeprefix("docker-pullable://")
    # containerd://sha256 alone can be an image-config ID, not a pullable manifest.
    return (
        cleaned if re.fullmatch(r"[a-z0-9][a-z0-9.:-]*(/[a-z0-9][a-z0-9._-]*)+@sha256:[0-9a-f]{64}", cleaned) else None
    )
