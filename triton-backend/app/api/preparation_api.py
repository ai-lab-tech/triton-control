"""Preparation HTTP boundary; handoff submission belongs to S03-A."""

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.engineering_queue import QueueEntry, TaskOverview
from app.schemas.operations import OperationResponse
from app.schemas.preparation import (
    AttemptDTO,
    InformationRequest,
    ReasonRequest,
    ReassignRequest,
    ResolveInputsRequest,
    TaskDTO,
    TaskEventDTO,
    TaskRevision,
)
from app.schemas.validation import ValidationManifest
from app.services import engineering_queue, preparation
from app.services.candidate_validation import attempts as validation

router = APIRouter(prefix="/api/preparation-tasks", tags=["preparation"])


@router.get("", response_model=list[TaskDTO])
@translate_app_errors
def list_tasks(
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TaskDTO]:
    return preparation.list_tasks(session, claims, limit, offset)


@router.get("/queue", response_model=list[QueueEntry])
@translate_app_errors
def queue(limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0, le=1000000),
          view: Literal["all", "mine", "unassigned"] | None = None,
          search: str = Query(default="", max_length=200),
          session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> list[QueueEntry]:
    return engineering_queue.queue(session, claims, limit, offset, view=view, search=search)


@router.get("/{task_id}/overview", response_model=TaskOverview)
@translate_app_errors
def overview(task_id: UUID, session: Session = Depends(get_session),
             claims: dict[str, Any] = Depends(get_claims)) -> TaskOverview:
    return engineering_queue.overview(session, claims, task_id)


@router.get("/{task_id}", response_model=TaskDTO)
@translate_app_errors
def get_task(
    task_id: UUID,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.get_task(session, claims, task_id)


@router.get("/{task_id}/events", response_model=list[TaskEventDTO])
@translate_app_errors
def list_events(
    task_id: UUID,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TaskEventDTO]:
    return preparation.list_events(session, claims, task_id, limit, offset)


@router.get("/{task_id}/attempts", response_model=list[AttemptDTO])
@translate_app_errors
def list_attempts(
    task_id: UUID,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=1000000),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[AttemptDTO]:
    return preparation.list_attempts(session, claims, task_id, limit, offset)


@router.post("/{task_id}/claim", response_model=TaskDTO)
@translate_app_errors
def claim(
    task_id: UUID,
    request: TaskRevision,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.claim(session, claims, task_id, request)


@router.post("/{task_id}/release-claim", response_model=TaskDTO)
@translate_app_errors
def release_claim(
    task_id: UUID,
    request: TaskRevision,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.release_claim(session, claims, task_id, request)


@router.post("/{task_id}/request-information", response_model=TaskDTO)
@translate_app_errors
def request_information(
    task_id: UUID,
    request: InformationRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.request_information(session, claims, task_id, request)


@router.post("/{task_id}/resolve-inputs", response_model=TaskDTO)
@translate_app_errors
def resolve_inputs(
    task_id: UUID,
    request: ResolveInputsRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.resolve_inputs(session, claims, task_id, request)


@router.post("/{task_id}/cancel", response_model=TaskDTO)
@translate_app_errors
def cancel(
    task_id: UUID,
    request: ReasonRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.cancel(session, claims, task_id, request)


@router.post("/{task_id}/reassign", response_model=TaskDTO)
@translate_app_errors
def reassign(
    task_id: UUID,
    request: ReassignRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TaskDTO:
    return preparation.reassign(session, claims, task_id, request)

@router.post("/{task_id}/validations", status_code=202, response_model=OperationResponse)
@translate_app_errors
def start_candidate_validation(
    task_id: UUID, manifest: ValidationManifest, response: Response,
    idempotency_key: str = Header(min_length=1, max_length=128), session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse:
    operation = validation.submit(session, claims, task_id, manifest, idempotency_key)
    response.headers["Location"] = operation.status_url
    return operation


@router.get("/{task_id}/validations", response_model=list[OperationResponse])
@translate_app_errors
def validation_history(
    task_id: UUID, limit: int = Query(default=10, ge=1, le=25), offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> list[OperationResponse]:
    return validation.history(session, claims, task_id, limit, offset)
