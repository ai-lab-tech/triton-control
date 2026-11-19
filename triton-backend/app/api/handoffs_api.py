"""Handoff and private in-app notification HTTP boundary."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.handoffs import HandoffReceipt, HandoffRequest, NotificationDTO
from app.schemas.operations import OperationResponse
from app.services import handoff_capture, handoffs

router = APIRouter(prefix="/api/preparation", tags=["handoffs"])


@router.post("/handoff-captures", response_model=OperationResponse, status_code=202)
@translate_app_errors
def capture(request: HandoffRequest, idempotency_key: str = Header(alias="Idempotency-Key"),
            session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> OperationResponse:
    return handoff_capture.submit(session, claims, request, idempotency_key)


@router.post("/handoffs", response_model=HandoffReceipt)
@translate_app_errors
def submit(
    request: HandoffRequest, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)
) -> HandoffReceipt:
    return handoffs.submit(session, claims, request)


@router.get("/handoffs/{handoff_id}", response_model=HandoffReceipt)
@translate_app_errors
def get(
    handoff_id: UUID, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)
) -> HandoffReceipt:
    return handoffs.get(session, claims, handoff_id)


@router.get("/notifications", response_model=list[NotificationDTO])
@translate_app_errors
def notifications(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=1000000),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[NotificationDTO]:
    return handoffs.notifications(session, claims, limit, offset)


@router.put("/notifications/{notification_id}/read", response_model=NotificationDTO)
@translate_app_errors
def read(
    notification_id: UUID, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)
) -> NotificationDTO:
    return handoffs.read_notification(session, claims, notification_id)
