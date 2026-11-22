"""Serialize development model commands and keep scaling behind a verified startup rollout."""

from collections.abc import Iterator
from contextlib import contextmanager
from ipaddress import ip_address
from typing import Any, Literal

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.core import installation_policy
from app.core.access_control import require_member_or_admin
from app.db.entities import TritonInstanceEntity
from app.exceptions import BadGatewayError, BadRequestError, ConflictError
from app.repositories import deployment_intents, instances, operations
from app.schemas.model_state import ModelState
from app.schemas.pods import DeploymentState, PodState
from app.services.access import get_instance_or_404
from app.services.deployment.model_state_provider import ROLLBACK_KEY, ModelStateProvider
from app.services.kubernetes_client import api_client


def managed(instance: TritonInstanceEntity) -> bool:
    return instance.is_self_deployed and instance.deployment_runtime == "kubernetes"


def converged(deployment: DeploymentState, pods: list[PodState], release: str | None) -> bool:
    count = deployment.desired
    return (
        not deployment.stale
        and deployment.updated == deployment.ready == deployment.available == len(pods) == count
        and count > 0
        and all(pod.ready and not pod.terminating and pod.current_revision
                and (release is None or pod.release == release) for pod in pods)
    )


def observe(
    session: Session, instance: TritonInstanceEntity, provider: ModelStateProvider,
) -> tuple[dict[str, Any], ModelState, list[PodState]]:
    namespace, name = instance.deployment_namespace, instance.deployment_name
    if not namespace or not name or instance.id is None:
        raise ConflictError("Deployment identity is unavailable")
    intent = deployment_intents.for_instance(session, instance.id)
    if intent:
        operation = operations.get(session, intent.operation_id)
        if intent.delete_requested or operation.state != "succeeded":
            raise ConflictError("Deployment creation or removal is in progress")
    raw = provider.deployment(namespace, name)
    deployment, owned_pods = provider.snapshot(namespace, name)
    pods = [pod for pod in owned_pods if pod.terminating or pod.phase not in {"Failed", "Succeeded"}]
    meta = raw["metadata"]
    previous = instance.model_state or {}
    expected_uid = intent.resource_uids.get(f"Deployment/{name}") if intent else previous.get("deployment_uid")
    if (intent and not expected_uid) or (expected_uid and expected_uid != meta["uid"]) or meta["uid"] != deployment.uid:
        raise ConflictError("Managed Deployment UID changed")
    if meta["generation"] != deployment.generation:
        raise ConflictError("Deployment changed during observation; refresh its state")
    annotations = raw["spec"]["template"]["metadata"].get("annotations") or {}
    release = deployment.release
    release_digest = release if release and release.startswith("sha256:") else None
    backup = annotations.get(ROLLBACK_KEY)
    production = installation_policy.mode() == "production"
    owner: Literal["manual", "source", "autoscaler"] = "source" if production else "manual"
    if not production and provider.has_autoscaler(namespace, name):
        owner = "autoscaler"
    stable = converged(deployment, pods, release)
    single = owner == "manual" and stable and deployment.desired == 1
    declared = production or release is not None or backup is not None
    failed = any(
        condition.type == "Progressing" and condition.status == "False"
        and condition.reason == "ProgressDeadlineExceeded" for condition in deployment.conditions
    )
    state = ModelState(
        capacity_owner=owner, mode="declared" if declared else "interactive",
        replicas=deployment.desired, release_digest=release_digest,
        rollout_complete=stable,
        interactive_allowed=single and not declared,
        can_declare=single and not declared,
        can_restore=(
            bool(backup) and owner == "manual" and deployment.desired == 1
            and (single or (failed and not deployment.stale and len(pods) <= 1))
        ),
        can_scale_above_one=owner == "manual" and stable and bool(release_digest and backup),
    )
    # Kubernetes is authoritative. Re-observe on every request so a crash or a
    # timed-out patch cannot leave the persisted mode at an unsafe older value.
    persisted = {
        "deployment_uid": meta["uid"], "capacity_owner": owner, "mode": state.mode, "replicas": state.replicas,
    }
    if persisted != previous:
        instance.model_state = persisted
        session.add(instance)
    return raw, state, pods


@contextmanager
def locked(session: Session, instance_id: int) -> Iterator[None]:
    try:
        instances.lock_model_state(session, instance_id)
        yield
        session.commit()
    except BaseException:
        session.rollback()
        raise


def interactive_endpoint(session: Session, instance: TritonInstanceEntity) -> str:
    try:
        session.refresh(instance)
        with api_client() as api:
            provider = ModelStateProvider(api)
            raw, state, pods = observe(session, instance, provider)
            if not state.interactive_allowed:
                raise ConflictError("Interactive control requires one stable pod in interactive mode")
            deployment = raw["metadata"]
            pod = provider.verify(
                deployment["namespace"], deployment["name"], deployment["uid"], pods[0],
                generation=deployment["generation"],
            )
            if pod["metadata"].get("deletionTimestamp") or not any(
                condition.get("type") == "Ready" and condition.get("status") == "True"
                for condition in pod.get("status", {}).get("conditions", [])
            ):
                raise ConflictError("The interactive pod is no longer ready")
            try:
                address = ip_address(pod.get("status", {}).get("podIP", ""))
            except ValueError as error:
                raise ConflictError("The interactive pod has no network address") from error
            host = f"[{address}]" if address.version == 6 else str(address)
            return f"http://{host}:8000"
    except ApiException as error:
        raise BadGatewayError("Could not verify the managed deployment before model control") from error


def get_state(session: Session, claims: dict[str, Any], instance_id: int) -> ModelState:
    return change(session, claims, instance_id, action="read")


def change(
    session: Session, claims: dict[str, Any], instance_id: int, *,
    action: Literal["read", "declare", "restore", "scale"], release_digest: str = "", replicas: int = 1,
) -> ModelState:
    if action != "read":
        installation_policy.require("direct_deployment")
        require_member_or_admin(claims)
    try:
        with locked(session, instance_id):
            instance = get_instance_or_404(session, instance_id, claims)
            if not managed(instance):
                raise BadRequestError("Model state and replica controls require a managed Kubernetes deployment")
            with api_client() as api:
                provider = ModelStateProvider(api)
                raw, state, _ = observe(session, instance, provider)
                if action == "read":
                    return state
                if state.capacity_owner != "manual":
                    raise ConflictError("Replica capacity is owned by another controller")
                if action == "declare" and state.release_digest == release_digest:
                    return state  # Safe replay after a successful or ambiguous patch.
                if action == "declare":
                    if not state.can_declare:
                        raise ConflictError("Declaring startup state requires one stable interactive pod")
                    provider.declare(raw, release_digest)
                elif action == "restore":
                    if state.mode == "interactive":
                        return state
                    if not state.can_restore:
                        raise ConflictError(
                            "Wait for one stable declared pod or a failed single-replica rollout before restoring"
                        )
                    provider.restore(raw, raw["spec"]["template"]["metadata"]["annotations"][ROLLBACK_KEY])
                elif action == "scale":
                    if replicas == state.replicas:
                        return state
                    if replicas > 1 and not state.can_scale_above_one:
                        raise ConflictError("Verify an explicitly prepared startup rollout before scaling above one")
                    provider.patch(raw, "/spec/replicas", replicas)
                _, accepted, _ = observe(session, instance, provider)
                return accepted
    except ApiException as error:
        if error.status in (409, 422):
            raise ConflictError("Kubernetes rejected the update; refresh the workload state") from error
        if error.status == 404:
            raise ConflictError("Managed Kubernetes deployment is unavailable") from error
        raise BadGatewayError("Could not read or update the managed Kubernetes deployment") from error
