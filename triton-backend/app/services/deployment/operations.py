"""Durable managed development deployment admission and adapter."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any
from uuid import uuid4

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.core.operation_secrets import decode, encode, fingerprint
from app.db.entities import DeploymentIntentEntity, OperationEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.repositories import deployment_intents as intents
from app.repositories import instances, operations, s3_profiles, users
from app.schemas import CreateDeploymentRequest, DeploymentResponse
from app.schemas.deployment_access import DeploymentAccessRequest
from app.schemas.operations import OperationResponse, ReconcileResult
from app.services.deployment import kubernetes as kube
from app.services.deployment.access import validate_new_hostname
from app.services.deployment.access_manifests import validate_policy
from app.services.deployment.examples import collect_deployment_examples
from app.services.deployment.operation_provider import OWNER, DeploymentProvider, OwnershipConflict, resource_key
from app.services.deployment.policy import resolve_hostname, validate_endpoint_policy, validate_ingress
from app.services.deployment.profile_request import resolve_profile_request, validate_deployment_token
from app.services.deployment.records import upsert_deployed_instance
from app.services.kubernetes_client import api_client, gpu_runtime_class_name, in_cluster_namespace
from app.services.operations import Action, ExecutionContext, service
from app.services.registry_settings import apply_registry_settings

logger = logging.getLogger(__name__)


def development_only() -> None:
    installation_policy.require("direct_deployment")
    if os.getenv("TRITON_CONTROL_MODE", "development") != "development":
        raise ForbiddenError("Interactive deployment creation is available only in development")


def timeout_seconds() -> int:
    return max(60, int(os.getenv("TRITON_DEPLOY_OPERATION_TIMEOUT_SECONDS", "1800")))


def input_data(request: CreateDeploymentRequest) -> dict[str, Any]:
    data = request.model_dump(mode="json")
    if request.lineage is None:
        data.pop("lineage", None)  # Preserve fingerprints and keyless retries from older clients.
    if request.basic_auth_password:
        data["basic_auth_password"] = request.basic_auth_password.get_secret_value()
    return data


def authorize_actor(actor: UserEntity) -> None:
    if not actor.is_active or actor.role.strip().lower() not in {"admin", "member"}:
        raise ForbiddenError("Active member or admin required")


def response(row: OperationEntity, intent: DeploymentIntentEntity) -> DeploymentResponse:
    return DeploymentResponse.model_validate({
        **intent.response, "operation_id": row.id, "status_url": f"/api/operations/{row.id}",
        "operation_state": row.state,
        "applied_resources": list(intent.resource_uids) if row.state == "succeeded" else [],
    })


def submit(
    request: CreateDeploymentRequest, session: Session, claims: dict[str, Any], key: str | None,
) -> DeploymentResponse:
    development_only()
    actor = require_user_entity(session, claims)
    authorize_actor(actor)
    validate_deployment_token(request, claims)
    digest = fingerprint(input_data(request))
    # Older clients retain their response fields; identical keyless retries are safe.
    key = key if key is not None else f"legacy-{digest}"
    if not key or len(key) > 128 or key != key.strip():
        raise BadRequestError("Invalid Idempotency-Key")
    target = request.deployment_name
    intents.lock_target(session, target)
    session.refresh(actor, with_for_update=True)
    authorize_actor(actor)
    previous = intents.replay(session, actor.id or 0, target, key)
    if previous:
        if previous.payload_hash != digest:
            raise ConflictError("Idempotency-Key already used with different deployment input")
        adapter.authorize_operation(session, actor, previous, "read")
        intent = intents.get(session, previous.id)
        if intent is None or instances.find_by_id(session, intent.instance_id) is None:
            raise ConflictError("Deployment was removed; use a new Idempotency-Key")
        return response(previous, intent)
    # Validate delegated-token restrictions even when the caller will execute asynchronously.
    request = resolve_hostname(request, session)
    validate_policy(request, creating=True)
    validate_endpoint_policy(request, session)
    validate_ingress(request)
    request = apply_registry_settings(request, session)
    request = resolve_profile_request(request, session, claims)
    validate_new_hostname(request)
    namespace = in_cluster_namespace() or target
    op_id = uuid4().hex
    runtime = None
    if request.gpu_count:
        with api_client() as api:
            runtime = gpu_runtime_class_name(api, request.gpu_count)
    manifests = kube._manifests(request, namespace, target, f"{target}-service",
                               f"{target}-s3-credentials", request.image, runtime_class_name=runtime)
    for manifest in manifests:
        manifest["metadata"].setdefault("labels", {})[OWNER] = op_id
        if manifest["kind"] == "Deployment":
            manifest["spec"]["template"]["metadata"].setdefault("labels", {})[OWNER] = op_id
    # Render exactly once, including generated basic-auth names and server configuration defaults.
    protected = encode({"request": input_data(request), "manifests": manifests,
                        "credential_version": actor.credential_version})
    stamp = operations.now(session)
    row = OperationEntity(id=op_id, actor_id=actor.id or 0, kind=intents.KIND, target=target,
                          idempotency_key=key, payload_hash=digest, payload={"deployment": target},
                          created_at=stamp, updated_at=stamp, available_at=stamp)
    try:
        session.add(row)
        session.flush()
        instance = upsert_deployed_instance(
            request, session, claims, namespace=namespace, deployment_name=target,
            service_name=f"{target}-service", secret_name=f"{target}-s3-credentials", image=request.image,
            triton_url=kube.pending_url(namespace, f"{target}-service"),
            metrics_url=kube.pending_metrics_url(namespace, f"{target}-service") if request.allow_metrics else None,
            initial_snapshot=kube.pending_snapshot(), commit=False,
        )
        result = DeploymentResponse(
            instance_id=instance.id, namespace=namespace, deployment_name=target, service_name=f"{target}-service",
            lineage=request.lineage,
            secret_name=f"{target}-s3-credentials", image=request.image, s3_url=request.s3_url, applied_resources=[],
            external_url=f"{request.ingress_scheme}://{request.ingress_host}" if request.ingress_host else None,
        )
        intent = DeploymentIntentEntity(operation_id=op_id, instance_id=instance.id, namespace=namespace,
                                        name=target, reservation=f"{namespace}/{target}", bundle_enc=protected,
                                        response=result.model_dump(mode="json"),
                                        deadline_at=stamp + timedelta(seconds=timeout_seconds()))
        session.add(intent)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("Deployment identity is already reserved") from None
    except Exception:
        session.rollback()
        raise
    session.refresh(row)
    session.refresh(intent)
    return response(row, intent)


def find_request(
    session: Session, claims: dict[str, Any], key: str, deployment_name: str,
) -> DeploymentResponse | None:
    actor = require_user_entity(session, claims)
    target = CreateDeploymentRequest.normalize_deployment_name(deployment_name)
    row = intents.replay(session, actor.id or 0, target, key)
    if row is None:
        return None
    service.checked(session, claims, row.id, "read")
    intent = intents.get(session, row.id)
    if intent is None or instances.find_by_id(session, intent.instance_id) is None:
        return None
    return response(row, intent)


def status(session: Session, claims: dict[str, Any], instance_id: int) -> OperationResponse | None:
    intent = intents.for_instance(session, instance_id)
    if intent is None:
        from app.services.access import get_instance_or_404
        get_instance_or_404(session, instance_id, claims)
        return None
    return service.read(session, claims, intent.operation_id)


def prepare_deletion(session: Session, instance_id: int) -> bool:
    """New managed instances use owned cleanup, never legacy namespace deletion."""
    installation_policy.require("direct_deployment")
    intent = intents.for_instance(session, instance_id)
    if intent is None:
        return False
    if not intents.retire(session, intent):
        raise ConflictError("Deployment cleanup requested; retry deletion after the creation operation is cancelled")
    return True


class DeploymentAdapter:
    def normalize(self, session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        raise BadRequestError("Submit deployment creation through /api/deployments")

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        instance = instances.find_by_name(session, target)
        if instance is None:
            raise ForbiddenError("Deployment instance no longer exists")
        if actor.role.strip().lower() != "admin" and target not in actor.assigned_instances:
            raise ForbiddenError("Deployment access revoked")
        if action != "read":
            authorize_actor(actor)
            # Cleanup of previously admitted work remains available after mode cutover.
            intent = intents.for_instance(session, instance.id or 0)
            row = operations.get(session, intent.operation_id) if intent else None
            cleanup = action == "cancel" or (action == "execute" and row is not None and row.cancel_requested)
            if not cleanup:
                development_only()

    def authorize_operation(
        self, session: Session, actor: UserEntity, operation: OperationEntity, action: Action,
    ) -> None:
        intent = intents.get(session, operation.id)
        instance = instances.find_by_id(session, intent.instance_id) if intent else None
        if instance is None:
            if action == "read" and (actor.id == operation.actor_id or actor.role.strip().lower() == "admin"):
                return
            raise ForbiddenError("Deployment instance no longer exists")
        self.authorize(session, actor, operation.target, action)

    def prepare_retry(self, session: Session, row: OperationEntity) -> None:
        intent = intents.get(session, row.id)
        if not intent or intent.cleaned or intent.delete_requested:
            raise ConflictError("A cleaned or deleting deployment cannot be retried")
        intents.renew_deadline(session, row.id, timeout_seconds())

    def reconcile(self, operation: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        installation_policy.require("direct_deployment")
        provider = DeploymentProvider()
        try:
            with context.sessions() as session:
                # Hold the row lock across bounded provider calls. Claimers skip it;
                # cancellation/deletion cannot race a write that is still in progress.
                current = operations.owned(session, operation.id, operation.worker or "", operation.generation)
                intent = intents.get(session, operation.id)
                if intent is None:
                    raise NotFoundError("Deployment intent missing")
                bundle = decode(intent.bundle_enc)
                actor = users.find_by_id(session, current.actor_id)
                context.service.authorize(session, actor, current.kind, current.target, "execute", current)
                instance = instances.find_by_id(session, intent.instance_id)
                if instance is None:
                    raise ForbiddenError("Deployment instance was removed")
                if actor and actor.credential_version != bundle["credential_version"]:
                    raise ForbiddenError("Submitting account credentials changed")
                if current.cancel_requested != operation.cancel_requested:
                    raise ConflictError("Cancellation requires a new pass")

                def guard() -> None:
                    leased = operations.owned(session, operation.id, operation.worker or "", operation.generation)
                    leased.lease_until = operations.now(session) + timedelta(seconds=30)
                    session.add(leased)
                    session.flush()
                    context.service.authorize(session, actor, current.kind, current.target, "execute", current)
                    if actor and actor.credential_version != bundle["credential_version"]:
                        raise ForbiddenError("Submitting account credentials changed")

                manifests = bundle["manifests"]
                try:
                    if not current.cancel_requested:
                        access_data = dict(bundle["request"])
                        if access_data.get("basic_auth_password"):
                            access_data["basic_auth_password"] = SecretStr(access_data["basic_auth_password"])
                        access = DeploymentAccessRequest.model_construct(**access_data)
                        validate_policy(access, creating=True)
                        validate_endpoint_policy(access, session)
                        validate_ingress(access)
                        if s3_profiles.get(session, bundle["request"]["s3_profile_id"]) is None:
                            raise ForbiddenError("Source S3 profile was removed")
                    if not current.cancel_requested:
                        provider.preflight(manifests)
                    outcome = self.advance(provider, current, intent, manifests, guard, session)
                except (BadRequestError, ApiException) as exc:
                    if isinstance(exc, ApiException) and exc.status not in {400, 401, 403, 422}:
                        raise
                    # Never expose provider bodies or protected manifest contents.
                    message = (
                        "Resource ownership conflict; inspect existing resources" if isinstance(exc, OwnershipConflict)
                        else "Deployment rejected; check configuration and Kubernetes permissions"
                    )
                    outcome = ReconcileResult(state="failed", message=message)
                if not current.cancel_requested and not intent.examples_captured:
                    try:
                        frozen_request = CreateDeploymentRequest.model_construct(**bundle["request"])
                        examples = collect_deployment_examples(frozen_request, instance)
                        if examples:
                            instance.inference_examples = examples
                    except Exception:
                        logger.info("Optional example capture unavailable for instance %s", intent.instance_id)
                    intent.examples_captured = True
                guard()
                instance.health_error = None if outcome.state == "succeeded" else outcome.message
                if outcome.state != "succeeded":
                    instance.health_ready = False
                instance.deployment_log = outcome.message
                session.add_all([intent, instance])
                session.commit()
                return outcome
        finally:
            provider.close()

    def advance(
        self, provider: DeploymentProvider, operation: OperationEntity, intent: DeploymentIntentEntity,
        manifests: list[dict[str, Any]], guard: Any, session: Session,
    ) -> ReconcileResult:
        installation_policy.require("direct_deployment")
        anchor: dict[str, Any] = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {
            "name": f"tc-operation-{operation.id}", "namespace": intent.namespace, "labels": {OWNER: operation.id},
        }, "data": {"operation": operation.id}}
        uids = dict(intent.resource_uids)
        if operation.cancel_requested:
            clean = True
            for manifest in reversed(manifests):
                clean = provider.delete(manifest, operation.id, uids.get(resource_key(manifest)), guard) and clean
            if not clean or provider.pods(intent.namespace, operation.id):
                return ReconcileResult(message="Stopping deployment; waiting for owned resources and pods")
            if not provider.delete(anchor, operation.id, uids.get(resource_key(anchor)), guard):
                return ReconcileResult(message="Removing deployment ownership record")
            intent.cleaned = True
            # Keep the reservation until the instance itself is deleted; the operation history remains.
            return ReconcileResult(state="cancelled", message="Owned deployment resources removed",
                                   result={"instance_id": intent.instance_id})
        if operations.now(session) >= intent.deadline_at:
            return ReconcileResult(state="failed",
                                   message="Deployment readiness deadline exceeded; inspect logs or retry")
        provider.namespace(intent.namespace, guard)
        actual_anchor = provider.ensure(anchor, operation.id, uids.get(resource_key(anchor)), guard)
        anchor_uid = actual_anchor["metadata"]["uid"]
        uids[resource_key(anchor)] = anchor_uid
        deployment = None
        for manifest in manifests:
            # Frozen manifests retain data; this only attaches the stable ownership UID.
            manifest["metadata"]["ownerReferences"] = [{"apiVersion": "v1", "kind": "ConfigMap",
                                                       "name": anchor["metadata"]["name"], "uid": anchor_uid}]
            actual = provider.ensure(manifest, operation.id, uids.get(resource_key(manifest)), guard)
            uids[resource_key(manifest)] = actual["metadata"]["uid"]
            if manifest["kind"] == "Deployment":
                deployment = actual
        intent.resource_uids = uids
        if deployment and provider.ready(deployment):
            return ReconcileResult(state="succeeded", message="Deployment reached configured serving readiness",
                                   result={"instance_id": intent.instance_id,
                                           "deployment_uid": deployment["metadata"]["uid"]})
        return ReconcileResult(message="Resources created; waiting for configured serving readiness",
                               result={"instance_id": intent.instance_id})


adapter = DeploymentAdapter()
