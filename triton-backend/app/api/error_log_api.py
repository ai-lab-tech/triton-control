"""Admin API for application error events."""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import ErrorEventDTO, FrontendErrorEventRequest
from app.services import error_logs

router = APIRouter(prefix="/api/admin/error-logs", tags=["error-logs"])


@router.get("", response_model=List[ErrorEventDTO])
@translate_app_errors
def list_error_logs(
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> List[ErrorEventDTO]:
    return error_logs.list_error_events(session, claims, source=source, limit=limit)


@router.post("/frontend", response_model=ErrorEventDTO)
@translate_app_errors
def create_frontend_error_log(
    request: FrontendErrorEventRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ErrorEventDTO:
    return error_logs.create_frontend_error_event(session, claims, request)
