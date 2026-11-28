"""Read Kubernetes evidence; never trust labels as an ownership boundary."""

from datetime import datetime, timezone
from math import ceil
from typing import Any, Literal

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError, ConflictError, NotFoundError
from app.schemas.pods import ContainerState, DeploymentState, PodCondition, PodState
from app.services.kubernetes_client import api_client

TIMEOUT = (3, 5)
RELEASE_KEY = "triton-control/release-id"
REVISION_KEY = "deployment.kubernetes.io/revision"


def controlled_by(obj: dict[str, Any], kind: str, uid: str) -> bool:
    return bool(uid) and any(
        ref.get("controller") is True and ref.get("kind") == kind and ref.get("uid") == uid
        for ref in obj.get("metadata", {}).get("ownerReferences", [])
    )


def conditions(status: dict[str, Any]) -> list[PodCondition]:
    return [
        PodCondition(**{k: row.get(k) for k in ("type", "status", "reason", "message")})
        for row in status.get("conditions", [])
    ]


def release(meta: dict[str, Any]) -> str | None:
    return (meta.get("annotations") or {}).get(RELEASE_KEY) or (meta.get("labels") or {}).get(RELEASE_KEY)


def container_states(pod: dict[str, Any]) -> list[ContainerState]:
    result = []
    for spec_key, status_key, default_kind in (
        ("initContainers", "initContainerStatuses", "init"),
        ("containers", "containerStatuses", "regular"),
    ):
        statuses = {s["name"]: s for s in pod.get("status", {}).get(status_key, [])}
        for spec in pod.get("spec", {}).get(spec_key, []):
            status = statuses.get(spec["name"], {})
            states = status.get("state") or {}
            state = next((s for s in ("waiting", "terminated", "running") if s in states), "unknown")
            detail = states.get(state) or {}
            kind: Literal["regular", "init", "sidecar"] = "regular"
            if default_kind == "init":
                kind = "sidecar" if spec.get("restartPolicy") == "Always" else "init"
            result.append(
                ContainerState(
                    name=spec["name"],
                    kind=kind,
                    ready=status.get("ready", False),
                    restart_count=status.get("restartCount", 0),
                    state=state,
                    reason=detail.get("reason"),
                    message=detail.get("message"),
                    exit_code=detail.get("exitCode"),
                    last_termination_reason=((status.get("lastState") or {}).get("terminated") or {}).get("reason"),
                    image=spec.get("image"),
                    image_id=status.get("imageID"),
                )
            )
    return result


def pod_state(pod: dict[str, Any], replica_set: dict[str, Any], revision: str | None) -> PodState:
    meta, status = pod["metadata"], pod.get("status", {})
    cs = container_states(pod)
    conds = conditions(status)
    failure = next((c for c in cs if c.state == "waiting" and c.reason), None)
    if failure is None:
        failure = next((c for c in cs if c.state == "terminated" and c.exit_code not in (None, 0)), None)
    condition = next((c for c in conds if c.type == "PodScheduled" and c.status == "False"), None)
    if condition is None:
        condition = next((c for c in conds if c.status == "False" and c.reason), None)
    rs_revision = (replica_set["metadata"].get("annotations") or {}).get(REVISION_KEY)
    terminating = bool(meta.get("deletionTimestamp"))
    return PodState(
        uid=meta["uid"],
        name=meta["name"],
        node=pod.get("spec", {}).get("nodeName"),
        phase=status.get("phase", "Unknown"),
        ready=not terminating and any(c.type == "Ready" and c.status == "True" for c in conds),
        terminating=terminating,
        created_at=meta.get("creationTimestamp"),
        restarts=sum(c.restart_count for c in cs),
        release=release(meta),
        revision=rs_revision,
        current_revision=bool(revision and rs_revision == revision),
        reason=status.get("reason")
        or (failure.reason if failure else None)
        or (condition.reason if condition else None),
        message=status.get("message")
        or (failure.message if failure else None)
        or (condition.message if condition else None),
        conditions=conds,
        containers=cs,
    )


class PodProvider:
    def __init__(self, api: Any) -> None:
        self.api = api
        self.apps = client.AppsV1Api(api)
        self.core = client.CoreV1Api(api)

    def data(self, obj: Any) -> dict[str, Any]:
        result: dict[str, Any] = self.api.sanitize_for_serialization(obj)
        return result

    def deployment(self, namespace: str, name: str) -> dict[str, Any]:
        return self.data(self.apps.read_namespaced_deployment(name, namespace, _request_timeout=TIMEOUT))

    def items(self, method: Any, namespace: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        token = None
        while True:
            page = self.data(method(namespace, limit=500, _continue=token, _request_timeout=TIMEOUT))
            result.extend(page.get("items", []))
            token = page.get("metadata", {}).get("continue")
            if not token:
                return result

    def snapshot(self, namespace: str, name: str) -> tuple[DeploymentState, list[PodState]]:
        deployment, pods, _ = self.workload_snapshot(namespace, name)
        return deployment, pods

    def workload_snapshot(
        self, namespace: str, name: str,
    ) -> tuple[DeploymentState, list[PodState], dict[str, dict[str, str]]]:
        deployment = self.deployment(namespace, name)
        meta, status = deployment["metadata"], deployment.get("status", {})
        sets = {
            r["metadata"]["uid"]: r
            for r in self.items(self.apps.list_namespaced_replica_set, namespace)
            if controlled_by(r, "Deployment", meta["uid"])
        }
        revision = (meta.get("annotations") or {}).get(REVISION_KEY)
        pods = []
        running_limits = {}
        for pod in self.items(self.core.list_namespaced_pod, namespace):
            owner = next((r for uid, r in sets.items() if controlled_by(pod, "ReplicaSet", uid)), None)
            if owner is not None:
                observed_pod = pod_state(pod, owner, revision)
                pods.append(observed_pod)
                if not observed_pod.terminating and any(
                    c.name == "triton" and c.kind == "regular" and c.state == "running" for c in observed_pod.containers
                ):
                    container = next(c for c in pod["spec"]["containers"] if c["name"] == "triton")
                    running_limits[observed_pod.name] = (container.get("resources") or {}).get("limits") or {}
        # Reads span several resources. Do not report a mixed generation as a completed rollout.
        after = self.deployment(namespace, name)
        if after["metadata"]["uid"] != meta["uid"]:
            raise ConflictError("Deployment was replaced during observation; refresh pod state")
        generation = meta.get("generation", 0)
        observed = status.get("observedGeneration")
        state = DeploymentState(
            uid=meta["uid"],
            name=meta["name"],
            generation=generation,
            observed_generation=observed,
            stale=observed is None or observed < generation or after["metadata"].get("generation") != generation,
            desired=deployment.get("spec", {}).get("replicas", 1),
            updated=status.get("updatedReplicas", 0),
            ready=status.get("readyReplicas", 0),
            available=status.get("availableReplicas", 0),
            release=release(deployment.get("spec", {}).get("template", {}).get("metadata", {})),
            revision=revision,
            conditions=conditions(status),
        )
        return state, sorted(pods, key=lambda p: (p.name, p.uid)), running_limits

    def verify(
        self, namespace: str, name: str, deployment_uid: str, pod: PodState, *, generation: int | None = None,
    ) -> dict[str, Any]:
        current = self.data(self.core.read_namespaced_pod(pod.name, namespace, _request_timeout=TIMEOUT))
        if current["metadata"]["uid"] != pod.uid:
            raise NotFoundError("Selected pod was removed or replaced; select a current pod")
        refs = current["metadata"].get("ownerReferences", [])
        owner = next((r for r in refs if r.get("controller") is True and r.get("kind") == "ReplicaSet"), None)
        if not owner:
            raise NotFoundError("Selected pod no longer belongs to this deployment")
        rs = self.data(self.apps.read_namespaced_replica_set(owner["name"], namespace, _request_timeout=TIMEOUT))
        if rs["metadata"]["uid"] != owner["uid"] or not controlled_by(rs, "Deployment", deployment_uid):
            raise NotFoundError("Selected pod no longer belongs to this deployment")
        metadata = self.deployment(namespace, name)["metadata"]
        if metadata["uid"] != deployment_uid:
            raise NotFoundError("Deployment was replaced; refresh pod state")
        if generation is not None and metadata["generation"] != generation:
            raise ConflictError("Deployment changed during observation; refresh pod state")
        return current

    def logs(
        self,
        namespace: str,
        deployment_name: str,
        deployment_uid: str,
        pod: PodState,
        container: str,
        previous: bool,
        since: datetime | None,
        lines: int,
        byte_limit: int,
    ) -> tuple[str, bool]:
        self.verify(namespace, deployment_name, deployment_uid, pod)
        response = self.core.read_namespaced_pod_log(
            pod.name,
            namespace,
            container=container,
            previous=previous,
            timestamps=True,
            follow=False,
            since_seconds=max(1, ceil((datetime.now(timezone.utc) - since).total_seconds())) if since else None,
            tail_lines=lines,
            limit_bytes=byte_limit,
            _request_timeout=TIMEOUT,
            _preload_content=False,
        )
        try:
            raw = response.read(byte_limit + 1)
        finally:
            response.close()
            response.release_conn()
        # Kubernetes logs use a name, not a UID. Discard the result if identity changed during the read.
        self.verify(namespace, deployment_name, deployment_uid, pod)
        return raw[:byte_limit].decode("utf-8", errors="ignore"), len(raw) >= byte_limit


def read_snapshot(namespace: str, name: str) -> tuple[DeploymentState, list[PodState]]:
    try:
        with api_client() as api:
            return PodProvider(api).snapshot(namespace, name)
    except ApiException as exc:
        if exc.status == 404:
            raise NotFoundError("Deployment no longer exists") from exc
        raise BadGatewayError("Could not read Kubernetes pod state") from exc
    except (ConflictError, NotFoundError):
        raise
    except Exception as exc:
        raise BadGatewayError("Could not read Kubernetes pod state") from exc


def read_legacy_logs(namespace: str, name: str) -> str:
    """Retain the legacy text contract without label-only access or unbounded reads."""
    try:
        with api_client() as api:
            provider = PodProvider(api)
            deployment, pods = provider.snapshot(namespace, name)
            chunks: list[str] = []
            remaining = 262144
            targets = [(pod, container) for pod in pods for container in pod.containers]
            for pod, container in targets[:16]:
                label = f"pod/{pod.name} uid/{pod.uid} container/{container.name}"
                if pod.phase in {"Failed", "Succeeded"}:
                    chunks.append(f"--- {label} status ---\n{pod.phase}: {pod.reason or ''} {pod.message or ''}")
                for previous in [True, False] if container.name == "triton" else [False]:
                    if remaining <= 0:
                        chunks.append("[Log byte limit reached]")
                        break
                    suffix = " previous" if previous else ""
                    try:
                        text, truncated = provider.logs(
                            namespace, name, deployment.uid, pod, container.name, previous, None, 300, remaining
                        )
                        remaining -= len(text.encode("utf-8"))
                        if text:
                            chunks.append(f"--- {label}{suffix} ---\n{text}")
                        if truncated:
                            chunks.append("[Log window truncated]")
                    except ApiException as exc:
                        if not (previous and exc.status in {400, 404}):
                            chunks.append(f"--- {label}{suffix} unavailable ---\nContainer logs unavailable")
                    except Exception:
                        chunks.append(f"--- {label}{suffix} unavailable ---\nPod changed or logs unavailable")
            if len(targets) > 16:
                chunks.append("[Container limit reached; use structured pod logs]")
            return "\n\n".join(chunks)
    except Exception as exc:
        raise BadGatewayError("Could not read owned Kubernetes pod logs") from exc
