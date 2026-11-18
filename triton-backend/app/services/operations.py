"""Reusable execution lifecycle; domain adapters own policy and reconciliation."""

from __future__ import annotations

import hashlib
import json
import logging
from threading import Event, Thread
from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.db.database import session_factory
from app.db.entities import OperationEntity, UserEntity
from app.exceptions import ConflictError, ForbiddenError, UnprocessableEntityError
from app.repositories import operations as repo
from app.repositories import users
from app.schemas.operations import OperationRequest, OperationResponse, ReconcileResult

Action = Literal["submit", "read", "cancel", "retry", "execute"]
logger = logging.getLogger(__name__)


class Adapter(Protocol):
    """Registered by application code, never selected as arbitrary executable code."""

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate domain input, apply defaults; return JSON without credential values."""
        ...

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        """Check current target access, team/mode/approval policy; raise on denial."""
        ...

    def reconcile(self, operation: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        """Observe by stable operation ID BEFORE writing; cancellation includes cleanup.

        Bound provider calls and use provider identity/preconditions to fence writes.
        Returning pending is safe after uncertain writes; never blindly repeat them.
        """
        ...


class OperationService:
    def __init__(self) -> None:
        self.adapters: dict[str, Adapter] = {}

    def register(self, kind: str, adapter: Adapter) -> None:
        if kind in self.adapters:
            raise ValueError(f"Duplicate operation adapter: {kind}")
        self.adapters[kind] = adapter

    def adapter(self, kind: str) -> Adapter:
        if kind not in self.adapters:
            raise UnprocessableEntityError("Operation kind is not enabled")
        return self.adapters[kind]

    def authorize(
        self, session: Session, actor: UserEntity | None, kind: str, target: str, action: Action,
        operation: OperationEntity | None = None,
    ) -> None:
        # All currently registered adapters execute development work. Production
        # reconciliation must use its independently approved admission contract.
        installation_policy.require("development_jobs")
        if actor is None or actor.id is None or not actor.is_active:
            raise ForbiddenError("Active user required")
        session.refresh(actor)
        if not actor.is_active or (action != "read" and actor.role.strip().lower() not in {"admin", "member"}):
            raise ForbiddenError("Current user role does not permit this operation")
        specific = getattr(self.adapter(kind), "authorize_operation", None)
        if specific and operation is not None:
            specific(session, actor, operation, action)
        else:
            self.adapter(kind).authorize(session, actor, target, action)

    def submit(
        self, session: Session, claims: dict[str, Any], request: OperationRequest, key: str,
    ) -> OperationResponse:
        if not key or len(key) > 128 or key != key.strip():
            raise UnprocessableEntityError("A nonempty Idempotency-Key of at most 128 characters is required")
        actor = require_user_entity(session, claims)
        self.authorize(session, actor, request.kind, request.target, "submit")
        payload = self.adapter(request.kind).normalize(request.payload)
        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise UnprocessableEntityError("Operation input must be finite JSON") from exc
        if len(canonical.encode()) > 65536:
            raise UnprocessableEntityError("Operation input exceeds 64 KiB; use artifact references")
        row = OperationEntity(id=uuid4().hex, actor_id=actor.id, kind=request.kind, target=request.target,
                              idempotency_key=key, payload=json.loads(canonical),
                              payload_hash=hashlib.sha256(canonical.encode()).hexdigest())
        # Committed intent IS the queue. No volatile scheduling callback is required.
        return dto(repo.reserve(session, row))

    def read(self, session: Session, claims: dict[str, Any], operation_id: str) -> OperationResponse:
        return dto(self.checked(session, claims, operation_id, "read"))

    def checked(
        self, session: Session, claims: dict[str, Any], operation_id: str, action: Action,
    ) -> OperationEntity:
        row = repo.get(session, operation_id)
        self.authorize(session, require_user_entity(session, claims), row.kind, row.target, action, row)
        return row

    def cancel(self, session: Session, claims: dict[str, Any], operation_id: str) -> OperationResponse:
        self.checked(session, claims, operation_id, "cancel")
        return dto(repo.cancel(session, operation_id))

    def retry(self, session: Session, claims: dict[str, Any], operation_id: str) -> OperationResponse:
        row = self.checked(session, claims, operation_id, "retry")
        row = repo.get(session, operation_id, lock=True)
        if row.state != "failed":
            raise ConflictError("Only failed operations can be retried")
        prepare = getattr(self.adapter(row.kind), "prepare_retry", None)
        if prepare:
            prepare(session, row)
        return dto(repo.retry(session, operation_id))


def dto(row: OperationEntity) -> OperationResponse:
    return OperationResponse.model_validate({
        **row.model_dump(), "status_url": f"/api/operations/{row.id}",
    })


class ExecutionContext:
    """Lease and live-policy check before external transitions, also usable as heartbeat."""

    def __init__(self, service: OperationService, sessions: Callable[[], Session], row: OperationEntity) -> None:
        self.service, self.sessions, self.row = service, sessions, row

    def checkpoint(self) -> None:
        with self.sessions() as session:
            current = repo.owned(session, self.row.id, self.row.worker or "", self.row.generation)
            self.service.authorize(session, users.find_by_id(session, current.actor_id),
                                   current.kind, current.target, "execute", current)
            if current.cancel_requested != self.row.cancel_requested:
                raise ConflictError("Cancellation requires a new reconciliation pass")
            repo.heartbeat(session, current.id, self.row.worker or "", self.row.generation, 30)


class OperationWorker:
    def __init__(self, service: OperationService, sessions: Callable[[], Session] = session_factory,
                 kinds: list[str] | None = None) -> None:
        self.service, self.sessions = service, sessions
        self.kinds = kinds
        self.identity = uuid4().hex
        self.stop_event = Event()
        self.thread: Thread | None = None

    def run_once(self) -> bool:
        if not installation_policy.allowed("development_jobs"):
            return False
        if not self.service.adapters:
            return False
        with self.sessions() as session:
            kinds = self.kinds if self.kinds is not None else list(self.service.adapters)
            row = repo.claim(session, self.identity, kinds, 30)
        if row is None:
            return False
        context = ExecutionContext(self.service, self.sessions, row)
        try:
            context.checkpoint()
            outcome = self.service.adapter(row.kind).reconcile(row, context)
            context.checkpoint()
        except ConflictError:
            # Lost lease or cancellation raced: never publish a stale result.
            return True
        except ForbiddenError:
            # Preserve intent, including cancellation, until access is restored.
            outcome = ReconcileResult(message="Execution blocked by current authorization", retry_after=60)
        except Exception:
            # Provider errors can contain credentials; never persist their raw text.
            outcome = ReconcileResult(message="External outcome uncertain; reconciliation will retry",
                                      retry_after=min(300, 2 ** min(row.attempts, 8)))
        try:
            with self.sessions() as session:
                repo.complete(session, row.id, self.identity, row.generation, outcome.state,
                              outcome.result, outcome.message, outcome.retry_after)
        except ConflictError:
            pass
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = Thread(target=self.loop, daemon=True, name="operation-reconciler")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.warning("Operation reconciliation interrupted; durable intent retained")
                worked = False
            self.stop_event.wait(0.1 if worked else 3)


service = OperationService()
worker = OperationWorker(service)
