"""Read-only instance workload evidence and bounded runtime logs."""
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.pods import InstancePods, PodLogs
from app.services.deployment import pods

router = APIRouter(prefix="/api/instances", tags=["pods"])


@router.get("/{instance_id}/pods", response_model=InstancePods)
@translate_app_errors
def instance_pods(instance_id: int, response: Response, session: Session = Depends(get_session),
                  claims: dict[str, Any] = Depends(get_claims)) -> InstancePods:
    response.headers["Cache-Control"] = "no-store"
    return pods.get_pods(session, claims, instance_id)


@router.get("/{instance_id}/pod-logs", response_model=PodLogs)
@translate_app_errors
def instance_pod_logs(
    instance_id: int, response: Response,
    pod_uid: str | None = Query(default=None, min_length=1, max_length=128),
    container: str | None = Query(default=None, min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9_.-]+$"),
    previous: bool = False, since: datetime | None = None,
    lines: int = Query(default=200, ge=1, le=2000),
    byte_limit: int = Query(default=262144, ge=1024, le=1048576),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> PodLogs:
    response.headers["Cache-Control"] = "no-store"
    return pods.get_logs(session, claims, instance_id, pod_uid=pod_uid, container=container,
                         previous=previous, since=since, lines=lines, byte_limit=byte_limit)
