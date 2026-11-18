"""Development experiment capture; shared operations expose progress/cancel/retry."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.experiment_snapshots import PreparationAccepted, PreparationRequest, SnapshotReceipt
from app.services.snapshots import service

router = APIRouter(tags=["experiment-snapshots"])


@router.post("/api/instances/{instance_id}/preparation-requests", status_code=202)
@translate_app_errors
def submit(
    instance_id: int,
    request: PreparationRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> PreparationAccepted:
    accepted = service.submit(session, claims, instance_id, request, idempotency_key)
    response.headers["Location"] = accepted.operation.status_url
    return accepted


@router.get("/api/preparation/snapshots/{snapshot_id}")
@translate_app_errors
def get(
    snapshot_id: UUID,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> SnapshotReceipt:
    return service.get(session, claims, snapshot_id)
