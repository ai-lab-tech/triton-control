"""Task-scoped submission and retrieval of durable validation attempts."""

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.core.identity import require_user_entity
from app.repositories import operations
from app.schemas.operations import OperationRequest, OperationResponse
from app.schemas.validation import ValidationManifest
from app.services.operations import dto, service


def submit(
    session: Session, claims: dict[str, Any], task_id: UUID, manifest: ValidationManifest, key: str
) -> OperationResponse:
    return service.submit(
        session,
        claims,
        OperationRequest(
            kind=operations.VALIDATION_KIND,
            target=str(task_id),
            payload=manifest.model_dump(mode="json", by_alias=True),
        ),
        key,
    )


def history(
    session: Session, claims: dict[str, Any], task_id: UUID, limit: int, offset: int
) -> list[OperationResponse]:
    service.authorize(session, require_user_entity(session, claims), operations.VALIDATION_KIND, str(task_id), "read")
    return [dto(row) for row in operations.validation_attempts(session, task_id, limit, offset)]
