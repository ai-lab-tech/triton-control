"""Durable handoff capture; large streaming hashes run outside browser requests."""

import time
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session

from app.core.identity import claims_from_user
from app.db.entities import OperationEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import operations, users
from app.schemas.handoffs import HandoffRequest
from app.schemas.operations import OperationRequest, OperationResponse, ReconcileResult
from app.services import handoffs
from app.services.operations import Action, ExecutionContext, service
from app.services.preparation_settings import effective

KIND = "handoff.capture"
TARGET = "model-preparation"


def submit(session: Session, claims: dict[str, Any], request: HandoffRequest, key: str) -> OperationResponse:
    handoffs._actor(session, claims)
    handoffs._previous(session, claims, request)
    handoffs._selectors(session, request)
    return service.submit(session, claims, OperationRequest(
        kind=KIND, target=TARGET, payload=request.model_dump(mode="json"),
    ), key)


class CaptureAdapter:
    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return HandoffRequest.model_validate(payload).model_dump(mode="json")
        except ValidationError:
            raise BadRequestError("Invalid handoff request") from None

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        if target != TARGET or not actor.is_active or actor.role.lower() not in {"member", "admin"}:
            raise ForbiddenError("Active member or administrator required for model capture")

    def authorize_operation(self, session: Session, actor: UserEntity, op: OperationEntity, action: Action) -> None:
        self.authorize(session, actor, op.target, action)
        if actor.id != op.actor_id and actor.role.lower() != "admin":
            raise ForbiddenError("Capture belongs to another user")
        if action in {"execute", "retry"} and not op.cancel_requested:
            handoffs._selectors(session, HandoffRequest.model_validate(op.payload))

    def reconcile(self, op: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        if op.cancel_requested:
            return ReconcileResult(state="cancelled", message="Capture cancelled")
        count, last = 0, 0.0

        def checkpoint(size: int) -> None:
            nonlocal count, last
            count += size
            now = time.monotonic()
            if now - last < 2:
                return
            context.checkpoint()
            with context.sessions() as progress_session:
                current = operations.owned(progress_session, op.id, op.worker or "", op.generation)
                current.result = {"bytes_verified": count}
                current.message = "Verifying exact source versions"
                progress_session.add(current)
                progress_session.commit()
            last = now

        def publish(session: Session, receipt_id: UUID) -> None:
            # Fence receipt/task publication and operation completion in one transaction.
            current = operations.owned(session, op.id, op.worker or "", op.generation)
            if current.cancel_requested:
                raise ConflictError("Capture cancelled before publication")
            current.state = "succeeded"
            current.result = {"handoff_id": str(receipt_id), "bytes_verified": count}
            current.message = "Model inputs verified"
            current.lease_until, current.worker = None, None
            session.add(current)

        with context.sessions() as session:
            actor = users.find_by_id(session, op.actor_id)
            if actor is None:
                raise ForbiddenError("Capture owner is unavailable")
            claims = claims_from_user(actor, {})
            # A whole-capture budget includes all three source roles.
            deadline = time.monotonic() + effective(session).capture_timeout_seconds

            def bounded_checkpoint(size: int) -> None:
                if time.monotonic() > deadline:
                    raise BadRequestError("Capture time limit exceeded; increase model preparation settings or retry")
                checkpoint(size)

            try:
                receipt = handoffs.submit(session, claims, HandoffRequest.model_validate(op.payload),
                                          checkpoint=bounded_checkpoint, publish=publish)
            except BadRequestError as error:
                return ReconcileResult(state="failed", message=str(error))
            except ConflictError:
                # Distinguish input mutation/conflicting revisions from a lost lease.
                context.checkpoint()
                return ReconcileResult(
                    state="failed", message="Inputs changed or revision conflicts; use a new revision",
                )
            return ReconcileResult(state="succeeded", result={"handoff_id": str(receipt.id), "bytes_verified": count},
                                   message="Model inputs verified")


adapter = CaptureAdapter()
