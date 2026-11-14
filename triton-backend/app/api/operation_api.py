"""HTTP boundary for registered durable operations."""

from typing import Any

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.operations import OperationRequest, OperationResponse
from app.services.operations import service

router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.post("", response_model=OperationResponse, status_code=202)
@translate_app_errors
def submit(
    request: OperationRequest, response: Response,
    idempotency_key: str = Header(min_length=1, max_length=128),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse:
    result = service.submit(session, claims, request, idempotency_key)
    response.headers["Location"] = result.status_url
    return result


@router.get("/{operation_id}", response_model=OperationResponse)
@translate_app_errors
def read(
    operation_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse:
    return service.read(session, claims, operation_id)


@router.post("/{operation_id}/cancel", response_model=OperationResponse, status_code=202)
@translate_app_errors
def cancel(
    operation_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse:
    return service.cancel(session, claims, operation_id)


@router.post("/{operation_id}/retry", response_model=OperationResponse, status_code=202)
@translate_app_errors
def retry(
    operation_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> OperationResponse:
    return service.retry(session, claims, operation_id)
