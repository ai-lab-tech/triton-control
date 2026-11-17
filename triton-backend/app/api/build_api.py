"""HTTP boundary for durable, task-owned development runtime builds."""

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.builds import BuildLogsResponse, BuildRequest, BuildResponse
from app.services.builds import execution

router = APIRouter(prefix="/api/builds", tags=["builds"])


@router.post("", response_model=BuildResponse, status_code=202)
@translate_app_errors
def submit(
    request: BuildRequest, response: Response,
    idempotency_key: str = Header(min_length=1, max_length=128),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildResponse:
    result = execution.submit(session, claims, request, idempotency_key)
    response.headers["Location"] = f"/api/builds/{result.id}"
    return result


@router.get("/{build_id}", response_model=BuildResponse)
@translate_app_errors
def read(
    build_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildResponse:
    return execution.read(session, claims, build_id)


@router.get("/{build_id}/logs", response_model=BuildLogsResponse)
@translate_app_errors
def logs(
    build_id: str, attempt: int = Query(default=1, ge=1),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildLogsResponse:
    return execution.logs(session, claims, build_id, attempt)


@router.post("/{build_id}/cancel", response_model=BuildResponse, status_code=202)
@translate_app_errors
def cancel(
    build_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildResponse:
    return execution.cancel(session, claims, build_id)
