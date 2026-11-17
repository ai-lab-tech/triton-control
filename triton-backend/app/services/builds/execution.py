"""Task authorization and durable reconciliation for qualified image-build attempts."""

import os
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session

from app.core.identity import claims_from_user
from app.db.entities import BuildAttemptEntity, OperationEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import build_contexts, builds, operations, preparation, users
from app.schemas.build_policy import BuildPolicy
from app.schemas.builds import BuildAttemptResponse, BuildLogsResponse, BuildRequest, BuildResponse
from app.schemas.operations import OperationRequest, ReconcileResult
from app.services import preparation as tasks
from app.services.builds import contexts, contexts_storage, jobs_kubernetes
from app.services.builds.qualification import require_qualified
from app.services.operations import Action, ExecutionContext, dto, service

KIND = "build.image"


def qualified(session: Session) -> BuildPolicy:
    settings = contexts_storage.policy()
    if settings.namespace in {"default", "kube-system", "kube-public", os.getenv("POD_NAMESPACE", "")}:
        raise ForbiddenError("Runtime builds require a dedicated development namespace")
    revision = os.getenv("TRITON_BUILD_FIXTURE_REVISION", "")
    if not revision:
        raise ForbiddenError("Configure the reviewed build fixture revision before enabling builds")
    require_qualified(session, settings, revision, mode=os.getenv("TRITON_CONTROL_MODE", "development"))
    return settings


def claims_guard(claims: dict[str, Any]) -> None:
    if claims.get("token_use"):
        raise ForbiddenError("Delegated deployment tokens cannot access runtime builds")


def checked(session: Session, claims: dict[str, Any], build_id: str, action: Action = "read") -> OperationEntity:
    claims_guard(claims)
    op = service.checked(session, claims, build_id, action)
    if op.kind != KIND:
        raise ForbiddenError("Not an image build operation")
    return op


def response(session: Session, op: OperationEntity) -> BuildResponse:
    return BuildResponse(id=op.id, task_id=UUID(op.target), actor_id=op.actor_id, operation=dto(op), attempts=[
        BuildAttemptResponse.model_validate(row.model_dump()) for row in builds.attempts(session, op.id)
    ])


def submit(session: Session, claims: dict[str, Any], request: BuildRequest, key: str) -> BuildResponse:
    claims_guard(claims)
    # Validate context ownership and qualification before accepting public API intent.
    settings = qualified(session)
    if request.repository not in settings.allowed_repositories:
        raise ForbiddenError("Destination repository is not allowed")
    if not contexts.read(session, claims, request.context_id).ready:
        raise ConflictError("A verified retained build context is required")
    result = service.submit(session, claims, OperationRequest(
        kind=KIND, target=str(request.task_id), payload=request.model_dump(mode="json"),
    ), key)
    return response(session, operations.get(session, result.id))


def read(session: Session, claims: dict[str, Any], build_id: str) -> BuildResponse:
    return response(session, checked(session, claims, build_id))


def cancel(session: Session, claims: dict[str, Any], build_id: str) -> BuildResponse:
    checked(session, claims, build_id, "cancel")
    return response(session, operations.cancel(session, build_id))


def logs(session: Session, claims: dict[str, Any], build_id: str, number: int) -> BuildLogsResponse:
    checked(session, claims, build_id)
    row = builds.get(session, build_id, number)
    if row.finished_at is not None:
        return BuildLogsResponse(attempt=number, output=row.logs, truncated=row.logs_truncated,
                                 expired=row.logs_expired, retained=True)
    provider = jobs_kubernetes.Jobs()
    try:
        resource = provider.read(row)
        observation = provider.observe(row, resource) if resource is not None else jobs_kubernetes.Observation()
        return BuildLogsResponse(attempt=number, output=observation.logs, truncated=observation.truncated,
                                 expired=False, retained=False)
    finally:
        provider.close()


def fence(session: Session, op: OperationEntity) -> None:
    current = operations.owned(session, op.id, op.worker or "", op.generation)
    if current.cancel_requested != op.cancel_requested:
        session.rollback()
        raise ConflictError("Cancellation requires a new reconciliation pass")


def admit(session: Session, op: OperationEntity, context: ExecutionContext) -> BuildAttemptEntity:
    existing = builds.latest(session, op.id)
    if existing is not None and not existing.retry_requested:
        return existing
    settings = qualified(session)
    request = BuildRequest.model_validate(op.payload["request"])
    if str(request.task_id) != op.target or settings.fingerprint() != op.payload["policy_hash"]:
        raise ForbiddenError("Build task or policy changed")
    actor = users.find_by_id(session, op.actor_id)
    if actor is None:
        raise ForbiddenError("Build actor no longer exists")
    receipt = contexts.consume_for_build(session, claims_from_user(actor, {}), request.context_id,
                                          os.environ["TRITON_BUILD_FIXTURE_REVISION"], context.checkpoint)
    fence(session, op)
    retained = build_contexts.get(session, request.context_id, lock=True)
    if retained.expired:
        raise ConflictError("Build context expired during admission")
    number = existing.number + 1 if existing else 1
    row = BuildAttemptEntity(
        operation_id=op.id, number=number, context_id=request.context_id,
        policy=settings.model_dump(mode="json"), context_receipt=receipt, executor_image=settings.executor_image,
        destination=f"{request.repository}:build-{op.id}-{number}", namespace=settings.namespace,
        job_name=f"build-{op.id}-{number}", created_at=operations.now(session),
        deadline_at=operations.now(session) + timedelta(seconds=settings.timeout_seconds + 120),
    )
    if existing:
        existing.retry_requested = False
        session.add(existing)
    session.add(row)
    session.commit()  # Persist unique identity BEFORE creating any external resources.
    session.refresh(row)
    return row


def outcome(row: BuildAttemptEntity) -> ReconcileResult:
    result = {"attempt": row.number, "job_uid": row.job_uid, "final_digest": row.final_digest,
              "image": row.destination.rsplit(":", 1)[0] + "@" + row.final_digest if row.final_digest else None}
    return ReconcileResult.model_validate({"state": row.state, "result": result, "message": "Build " + row.state})


class BuildAdapter:
    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = BuildRequest.model_validate(payload)
        except ValidationError:
            raise BadRequestError("Invalid image build request") from None
        policy = contexts_storage.policy()
        if request.repository not in policy.allowed_repositories:
            raise ForbiddenError("Destination repository is not allowed")
        return {"request": request.model_dump(mode="json"), "policy_hash": policy.fingerprint()}

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        contexts.authorize_actor(session, actor)
        try:
            task_id = UUID(target)
        except ValueError:
            raise BadRequestError("Build target must be a preparation task ID") from None
        _, admin, eligible = tasks.authorize_task_access(session, claims_from_user(actor, {}), task_id)
        if not (admin or eligible):
            raise ForbiddenError("Current task engineering membership required")
        if action in {"submit", "retry"}:
            qualified(session)
            if preparation.get(session, task_id).state == "cancelled":
                raise ConflictError("Cannot build for a cancelled task")

    def authorize_operation(self, session: Session, actor: UserEntity, op: OperationEntity, action: Action) -> None:
        self.authorize(session, actor, op.target, action)
        if actor.id != op.actor_id and actor.role.strip().lower() != "admin":
            raise ForbiddenError("Build belongs to another user")

    def prepare_retry(self, session: Session, op: OperationEntity) -> None:
        row = builds.latest(session, op.id)
        if row is None or not row.cleaned or row.state != "failed":
            raise ConflictError("Build cleanup must finish before retry")
        # A new attempt is journaled on the next worker pass; old evidence stays intact.
        row.retry_requested = True
        session.add(row)

    def reconcile(self, op: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        with context.sessions() as session:
            row = builds.latest(session, op.id)
            stopping = op.cancel_requested or preparation.get(session, UUID(op.target)).state == "cancelled"
            if stopping and (row is None or row.retry_requested):
                return ReconcileResult(state="cancelled", message="Build cancelled before execution")
            if row is None or row.retry_requested:
                actor = users.find_by_id(session, op.actor_id)
                if actor is None:
                    raise ForbiddenError("Build actor no longer exists")
                request = BuildRequest.model_validate(op.payload["request"])
                if not contexts.read(session, claims_from_user(actor, {}), request.context_id).ready:
                    return ReconcileResult(state="failed", message="Build context expired before admission")
                try:
                    row = admit(session, op, context)
                except BadRequestError:
                    return ReconcileResult(state="failed", message="Retained build context verification failed")
            session.expunge(row)
            session.rollback()
            provider = jobs_kubernetes.Jobs()
            try:
                return reconcile_attempt(session, op, row, context, provider)
            finally:
                provider.close()


def reconcile_attempt(
    session: Session, op: OperationEntity, row: BuildAttemptEntity, context: ExecutionContext,
    provider: jobs_kubernetes.Jobs,
) -> ReconcileResult:
    context.checkpoint()
    fence(session, op)
    if row.finished_at is not None:
        # The result/log transaction survived, even if API or worker died before cleanup.
        if not row.cleaned:
            row.cleaned = provider.cleanup(row)
            session.add(row)
            session.commit()
        else:
            session.rollback()
        if row.cleaned and op.cancel_requested:
            return ReconcileResult(state="cancelled", result=outcome(row).result, message="Build cancelled")
        return outcome(row) if row.cleaned else ReconcileResult(message="Waiting for owned Job cleanup")
    resource = provider.read(row)
    expired = operations.now(session) >= row.deadline_at
    stopping = op.cancel_requested or preparation.get(session, UUID(op.target)).state == "cancelled"
    if resource is None and not row.job_uid and not stopping and not expired:
        settings = qualified(session)
        if settings.fingerprint() != op.payload["policy_hash"]:
            raise ForbiddenError("Build policy changed before execution")
        receipt = row.context_receipt
        with contexts_storage.storage(session, receipt["storage_binding"]) as store:
            url = store.download_url(receipt["key"], receipt["version_id"], settings.timeout_seconds + 180)
            ca_certificate = store.ca_certificate
        provider.provision(row, url, ca_certificate)
        resource = provider.create(row)  # Suspended: cannot start until its UID is committed.
        row.job_uid = resource.metadata.uid
        session.add(row)
        session.commit()
        return ReconcileResult(message="Owned build Job reserved", retry_after=1)
    if resource is not None and row.job_uid is None:
        row.job_uid = resource.metadata.uid  # Recover create whose acknowledgement was lost.
        session.add(row)
        session.commit()
        return ReconcileResult(message="Recovered owned build Job", retry_after=1)
    observation = provider.observe(row, resource) if resource is not None else jobs_kubernetes.Observation()
    if stopping or expired or observation.terminal or (resource is None and row.job_uid):
        row.state = "cancelled" if stopping else "succeeded" if observation.succeeded else "failed"
        row.final_digest = observation.digest if row.state == "succeeded" else None
        row.logs, row.logs_truncated = observation.logs, observation.truncated
        row.finished_at = operations.now(session)
        row.logs_expires_at = row.finished_at + timedelta(days=30)
        session.add(row)
        session.commit()  # Retention MUST precede deletion of Pods and credential Secrets.
        return ReconcileResult(message="Build evidence retained; cleanup pending", retry_after=1)
    if resource is not None and resource.spec.suspend:
        policy = qualified(session)
        if policy.fingerprint() != op.payload["policy_hash"]:
            raise ForbiddenError("Build policy changed before execution")
        provider.resume(row, resource)
    session.rollback()
    return ReconcileResult(message="Build executor running")


adapter = BuildAdapter()
