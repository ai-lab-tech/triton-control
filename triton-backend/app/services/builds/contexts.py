"""Authenticated staged uploads backed by the shared durable operation lifecycle."""

import hashlib
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlmodel import Session

from app.db.entities import BuildContextEntity, OperationEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import build_contexts as repo
from app.repositories import operations, teams
from app.schemas.build_contexts import BuildContextRequest, BuildContextResponse, ContextManifest
from app.schemas.operations import OperationRequest, ReconcileResult
from app.services.builds import contexts_storage as provider
from app.services.builds.contexts_archive import prepare
from app.services.builds.qualification import require_qualified
from app.services.operations import Action, ExecutionContext, dto, service

KIND = "build.context.stage"
TARGET = "build-context"


def authorize_actor(session: Session, actor: UserEntity) -> None:
    session.refresh(actor)
    role = actor.role.strip().lower()
    if not actor.is_active or role not in {"admin", "member"}:
        raise ForbiddenError("Active member or admin required")
    if role != "admin" and actor.id not in teams.eligible_user_ids(session, teams.INSTALLATION_TEAM_ID):
        raise ForbiddenError("Current installation engineering membership required")


def ensure(session: Session, op: OperationEntity) -> BuildContextEntity:
    if session.get(BuildContextEntity, op.id) is not None:
        return repo.get(session, op.id)
    settings = provider.policy()
    if settings.fingerprint() != op.payload["policy_hash"]:
        raise ForbiddenError("Build policy changed; submit a new context under the current policy")
    if session.get(BuildContextEntity, op.id) is None:
        return repo.ensure(
            session, op.id, policy_hash=settings.fingerprint(), storage_binding=provider.binding(session, settings),
            expires_at=op.created_at + timedelta(seconds=op.payload["retention_seconds"]),
            upload_until=op.created_at + timedelta(minutes=30),
        )
    return repo.get(session, op.id)


def checked(session: Session, claims: dict[str, Any], context_id: str, action: Action = "read") -> OperationEntity:
    if claims.get("token_use"):
        raise ForbiddenError("Delegated deployment tokens cannot stage build contexts")
    op = service.checked(session, claims, context_id, action)
    if op.kind != KIND:
        raise ForbiddenError("Not a build context operation")
    return op


def response(session: Session, op: OperationEntity) -> BuildContextResponse:
    row = ensure(session, op)
    receipt = repo.receipt(session, op.id)
    return BuildContextResponse(
        id=op.id, operation=dto(op), upload_url=f"/api/build-contexts/{op.id}/archive", expires_at=row.expires_at,
        ready=bool(receipt and not row.expired and row.expires_at > operations.now(session)
                   and op.state == "succeeded"),
        manifest=ContextManifest.model_validate(receipt.payload["manifest"]) if receipt else None,
    )


def submit(
    session: Session, claims: dict[str, Any], request: BuildContextRequest, key: str,
) -> BuildContextResponse:
    if claims.get("token_use"):
        raise ForbiddenError("Delegated deployment tokens cannot stage build contexts")
    result = service.submit(session, claims, OperationRequest(kind=KIND, target=TARGET,
                                                            payload=request.model_dump(mode="json")), key)
    return response(session, operations.get(session, result.id))


def read(session: Session, claims: dict[str, Any], context_id: str) -> BuildContextResponse:
    return response(session, checked(session, claims, context_id))


def begin_upload(session: Session, claims: dict[str, Any], context_id: str) -> tuple[str, BuildContextRequest, int]:
    op = checked(session, claims, context_id, "submit")
    ensure(session, op)
    op = operations.get(session, context_id, lock=True)
    row = repo.get(session, context_id, lock=True)
    stamp = operations.now(session)
    if op.state in operations.TERMINAL or op.cancel_requested or row.expired or row.expires_at <= stamp:
        raise ConflictError("Build context is no longer accepting uploads")
    if row.manifest is not None:
        raise ConflictError("Build context already uploaded; poll its operation")
    if row.upload_token is None and row.upload_until and row.upload_until <= stamp:
        raise ConflictError("Build context upload deadline exceeded; retry the failed operation")
    if row.upload_token and row.upload_until and row.upload_until > stamp:
        raise ConflictError("A build context upload is already in progress")
    token = uuid4().hex
    row.upload_token, row.upload_until = token, stamp + timedelta(minutes=5)
    session.add(row)
    session.commit()
    return token, BuildContextRequest.model_validate(op.payload["request"]), provider.policy().max_context_bytes


def upload_guard(session: Session, claims: dict[str, Any], context_id: str, token: str) -> BuildContextEntity:
    op = checked(session, claims, context_id, "submit")
    if op.state in operations.TERMINAL or op.cancel_requested:
        raise ConflictError("Build context upload cancelled or completed")
    row = repo.get(session, context_id, lock=True)
    stamp = operations.now(session)
    if (row.upload_token != token or row.upload_until is None or row.upload_until <= stamp
            or row.expired or row.expires_at <= stamp):
        raise ConflictError("Build context upload lease lost")
    return row


def abort_upload(session: Session, context_id: str, token: str) -> None:
    session.rollback()
    row = repo.get(session, context_id, lock=True)
    if row.upload_token == token and row.manifest is None:
        row.upload_token = None
        session.add(row)
    session.commit()


def finish_upload(
    session: Session, claims: dict[str, Any], context_id: str, token: str, source: BinaryIO,
) -> BuildContextResponse:
    op = checked(session, claims, context_id, "submit")
    request = BuildContextRequest.model_validate(op.payload["request"])
    settings = provider.policy()
    hasher = hashlib.sha256()
    size = 0
    source.seek(0)
    while chunk := source.read(65536):
        size += len(chunk)
        if size > min(request.archive_bytes, settings.max_context_bytes):
            raise BadRequestError("Build archive exceeds declared size or policy limit")
        hasher.update(chunk)
    if size != request.archive_bytes or "sha256:" + hasher.hexdigest() != request.archive_digest:
        raise BadRequestError("Build archive is incomplete or its checksum differs")
    source.seek(0)
    last_check = 0.0

    def checkpoint() -> None:
        nonlocal last_check
        if time.monotonic() - last_check >= 1:
            upload_guard(session, claims, context_id, token)
            session.rollback()
            last_check = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="triton-build-context-") as scratch, tempfile.TemporaryFile() as output:
        manifest = prepare(source, Path(scratch), output, request, settings.max_context_bytes, checkpoint)
        row = upload_guard(session, claims, context_id, token)
        row.manifest = manifest.model_dump(mode="json")
        key = str(row.storage_binding["prefix"]) + context_id + "/" + token + "/context.tar.gz"
        row.object_key = key
        session.add(row)
        session.commit()  # Intent before the S3 write: recovery can locate an unacknowledged version.
        binding = dict(row.storage_binding)
        session.rollback()
        with provider.storage(session, binding) as store:
            version = store.put(key, output, manifest)
        row = upload_guard(session, claims, context_id, token)
        row.object_version = version
        session.add(row)
        session.commit()
    return read(session, claims, context_id)


class ContextAdapter:
    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            request = BuildContextRequest.model_validate(payload)
        except ValidationError:
            raise BadRequestError("Invalid build context request") from None
        settings = provider.policy()
        if request.archive_bytes > settings.max_context_bytes:
            raise BadRequestError("Build archive exceeds installation byte limit")
        return {"request": request.model_dump(mode="json"), "policy_hash": settings.fingerprint(),
                "retention_seconds": provider.retention_seconds()}

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        if target != TARGET:
            raise ForbiddenError("Invalid build context target")
        authorize_actor(session, actor)
        if action not in {"read", "cancel", "execute"}:
            provider.policy()

    def authorize_operation(self, session: Session, actor: UserEntity, op: OperationEntity, action: Action) -> None:
        self.authorize(session, actor, op.target, action)
        if actor.id != op.actor_id and actor.role.strip().lower() != "admin":
            raise ForbiddenError("Build context belongs to another user")
        cleanup = action == "execute" and op.cancel_requested
        if (action not in {"read", "cancel"} and not cleanup
                and provider.policy().fingerprint() != op.payload["policy_hash"]):
            raise ForbiddenError("Build policy changed; submit a new context")

    def prepare_retry(self, session: Session, op: OperationEntity) -> None:
        row = repo.get(session, op.id, lock=True)
        if row.expired or row.expires_at <= operations.now(session) or repo.receipt(session, op.id):
            raise ConflictError("Expired or verified build contexts cannot be retried")
        row.manifest, row.object_key, row.object_version, row.upload_token = None, None, None, None
        row.upload_until = operations.now(session) + timedelta(minutes=30)
        session.add(row)

    def reconcile(self, op: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        with context.sessions() as session:
            row = ensure(session, op)
            binding = dict(row.storage_binding)
            prefix = binding["prefix"] + op.id + "/"
            if op.cancel_requested:
                # Fence the upload BEFORE cleanup; running upload will fail its next guard.
                row = repo.get(session, op.id, lock=True)
                row.upload_token = None
                session.add(row)
                session.commit()
                if (row.manifest is not None and row.object_version is None and row.upload_until
                        and row.upload_until + timedelta(seconds=30) > operations.now(session)):
                    return ReconcileResult(
                        message="Waiting for interrupted upload to stop before cleanup", retry_after=5,
                    )
                with provider.storage(session, binding) as store:
                    store.delete_prefix(prefix, context.checkpoint)
                return ReconcileResult(state="cancelled", message="Build context upload cancelled")
            if row.expired or row.expires_at <= operations.now(session):
                return ReconcileResult(state="failed", message="Build context expired")
            if row.manifest is None:
                if row.upload_until and row.upload_until <= operations.now(session):
                    return ReconcileResult(state="failed", message="Build context upload deadline exceeded")
                return ReconcileResult(message="Waiting for build archive upload", retry_after=10)
            manifest = ContextManifest.model_validate(row.manifest)
            key, version = row.object_key, row.object_version
            if key is None:
                return ReconcileResult(state="failed", message="Build context staging is incomplete")
            if version is None and row.upload_until and row.upload_until > operations.now(session):
                return ReconcileResult(message="Build archive is being staged", retry_after=5)
            session.rollback()
            with provider.storage(session, binding) as store:
                try:
                    if version is None:
                        version = store.recover(key, manifest, context.checkpoint)
                        if version is None:
                            return ReconcileResult(
                                state="failed", message="Build context staging was interrupted; reupload",
                            )
                    store.verify(key, version, manifest, context.checkpoint)
                except BadRequestError:
                    return ReconcileResult(state="failed", message="Retained build context verification failed")
            context.checkpoint()
            current = operations.owned(session, op.id, op.worker or "", op.generation)
            row = repo.get(session, op.id, lock=True)
            if current.cancel_requested or row.expired or row.expires_at <= operations.now(session):
                raise ConflictError("Build context changed during verification")
            row.object_version = version
            session.add(row)
            repo.publish(session, op.id, {
                "manifest": manifest.model_dump(mode="json"), "dockerfile": op.payload["request"]["dockerfile"],
                "policy_hash": row.policy_hash, "storage_binding": binding, "key": key, "version_id": version,
            })
            return ReconcileResult(state="succeeded", result={"context_id": op.id}, message="Build context verified")


def consume_for_build(
    session: Session, claims: dict[str, Any], context_id: str, fixture_revision: str,
    checkpoint: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """S06-C admission hook. Reverify exact retained version before executor startup.

    S06-C must keep the context retained for its complete Job/retry lifetime.
    This hook does not create Jobs or assert site-secret provisioning is valid.
    """
    op = checked(session, claims, context_id, "execute")
    settings = provider.policy()
    require_qualified(session, settings, fixture_revision, mode="development")
    row = repo.get(session, context_id, lock=True)
    receipt = repo.receipt(session, context_id)
    if op.state != "succeeded" or receipt is None or row.expired or row.expires_at <= operations.now(session):
        raise ConflictError("A verified, retained build context is required")
    payload = dict(receipt.payload)
    with provider.storage(session, payload["storage_binding"]) as store:
        store.verify(payload["key"], payload["version_id"],
                     ContextManifest.model_validate(payload["manifest"]), checkpoint)
    # Reserve enough retention for a Job admitted immediately after this hook.
    # Each later S06-C retry must pass admission again before starting its Job.
    row.expires_at = max(row.expires_at, operations.now(session) + timedelta(seconds=settings.timeout_seconds + 300))
    session.add(row)
    session.commit()
    return payload


adapter = ContextAdapter()
