"""Observe controller ownership by UID; labels are never authority."""

from typing import Any, Literal, cast

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError, ConflictError, NotFoundError
from app.schemas.pods import Condition, ContainerState, DeploymentState, PodState
from app.services.kubernetes_client import api_client


def _owner(resource: dict[str, Any], kind: str, uids: set[str]) -> bool:
    return any(
        ref.get("controller") is True and ref.get("kind") == kind and ref.get("uid") in uids
        for ref in resource.get("metadata", {}).get("ownerReferences", [])
    )


def _conditions(status: dict[str, Any]) -> list[Condition]:
    return [Condition.model_validate(item) for item in status.get("conditions", [])]


def _containers(pod: dict[str, Any]) -> list[ContainerState]:
    result = []
    for kind, specs, statuses in (
        ("regular", "containers", "containerStatuses"),
        ("init", "initContainers", "initContainerStatuses"),
    ):
        by_name = {s["name"]: s for s in pod.get("status", {}).get(statuses, [])}
        for spec in pod.get("spec", {}).get(specs, []):
            status = by_name.get(spec["name"], {})
            state = status.get("state", {})
            state_name = next((key for key in ("waiting", "running", "terminated") if key in state), "unknown")
            details = state.get(state_name, {})
            result.append(
                ContainerState(
                    name=spec["name"],
                    kind=cast(Literal["regular", "init"], kind),
                    image=spec["image"],
                    image_id=status.get("imageID"),
                    ready=status.get("ready", False),
                    restarts=status.get("restartCount", 0),
                    state=cast(Literal["waiting", "running", "terminated", "unknown"], state_name),
                    reason=details.get("reason"),
                    message=details.get("message"),
                    exit_code=details.get("exitCode"),
                    last_termination_reason=status.get("lastState", {}).get("terminated", {}).get("reason"),
                )
            )
    return result


def owned_state(
    deployment: dict[str, Any],
    replicasets: list[dict[str, Any]],
    pods: list[dict[str, Any]],
) -> tuple[DeploymentState, list[PodState]]:
    """Pure translation keeps old rollout ReplicaSets and terminating pods visible."""
    metadata, status = deployment["metadata"], deployment.get("status", {})
    sets = {rs["metadata"]["uid"]: rs for rs in replicasets if _owner(rs, "Deployment", {metadata["uid"]})}
    observed = status.get("observedGeneration")
    summary = DeploymentState(
        uid=metadata["uid"],
        generation=metadata["generation"],
        observed_generation=observed,
        awaiting_observation=observed is None or observed < metadata["generation"],
        desired=deployment.get("spec", {}).get("replicas", 1),
        updated=status.get("updatedReplicas", 0),
        ready=status.get("readyReplicas", 0),
        available=status.get("availableReplicas", 0),
        conditions=_conditions(status),
    )
    result = []
    for pod in pods:
        if not _owner(pod, "ReplicaSet", set(sets)):
            continue
        meta, current = pod["metadata"], pod.get("status", {})
        conditions = _conditions(current)
        rs = next(rs for uid, rs in sets.items() if _owner(pod, "ReplicaSet", {uid}))
        result.append(
            PodState(
                uid=meta["uid"],
                name=meta["name"],
                node=pod.get("spec", {}).get("nodeName"),
                phase=current.get("phase", "Unknown"),
                ready=any(c.type == "Ready" and c.status == "True" for c in conditions),
                created_at=meta.get("creationTimestamp"),
                deletion_timestamp=meta.get("deletionTimestamp"),
                revision=rs["metadata"].get("annotations", {}).get("deployment.kubernetes.io/revision"),
                reason=current.get("reason"),
                message=current.get("message"),
                conditions=conditions,
                containers=_containers(pod),
            )
        )
    return summary, sorted(result, key=lambda item: (item.name, item.uid))


def read_owned_state(namespace: str, name: str) -> tuple[DeploymentState, list[PodState]]:
    try:
        with api_client() as api:
            apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
            deployment = api.sanitize_for_serialization(apps.read_namespaced_deployment(name, namespace))
            sets = api.sanitize_for_serialization(apps.list_namespaced_replica_set(namespace))["items"]
            pods = api.sanitize_for_serialization(core.list_namespaced_pod(namespace))["items"]
            latest = apps.read_namespaced_deployment(name, namespace)
            if (
                latest.metadata.uid != deployment["metadata"]["uid"]
                or latest.metadata.generation != deployment["metadata"]["generation"]
            ):
                raise ConflictError("Deployment changed during observation; refresh pod state")
            return owned_state(deployment, sets, pods)
    except ApiException as exc:
        if exc.status == 404:
            raise NotFoundError("Managed Kubernetes deployment not found") from exc
        raise BadGatewayError("Kubernetes pod observation failed") from exc
    except (ConflictError, NotFoundError):
        raise
    except Exception as exc:
        raise BadGatewayError("Kubernetes pod observation unavailable") from exc
