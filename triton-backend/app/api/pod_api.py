"""Instance workload evidence, bounded logs, and model-state controls."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.model_state import DeclareModelState, ModelState, ScaleModelState
from app.schemas.pods import InstancePods, PodLogs
from app.services.deployment import model_state, pods

router = APIRouter(prefix="/api/instances", tags=["pods"])


@router.get("/{instance_id}/pods", response_model=InstancePods)
@translate_app_errors
def instance_pods(
    instance_id: int,
    response: Response,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> InstancePods:
    response.headers["Cache-Control"] = "no-store"
    return pods.get_pods(session, claims, instance_id)


@router.get("/{instance_id}/pod-logs", response_model=PodLogs)
@translate_app_errors
def instance_pod_logs(
    instance_id: int,
    response: Response,
    pod_uid: str | None = Query(default=None, min_length=1, max_length=128),
    container: str | None = Query(default=None, min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9_.-]+$"),
    previous: bool = False,
    since: datetime | None = None,
    lines: int = Query(default=200, ge=1, le=2000),
    byte_limit: int = Query(default=262144, ge=1024, le=1048576),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> PodLogs:
    response.headers["Cache-Control"] = "no-store"
    return pods.get_logs(
        session,
        claims,
        instance_id,
        pod_uid=pod_uid,
        container=container,
        previous=previous,
        since=since,
        lines=lines,
        byte_limit=byte_limit,
    )


@router.get("/{instance_id}/model-state", response_model=ModelState)
@translate_app_errors
def instance_model_state(
    instance_id: int,
    response: Response,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelState:
    response.headers["Cache-Control"] = "no-store"
    return model_state.get_state(session, claims, instance_id)


@router.post("/{instance_id}/model-state/declare", response_model=ModelState, status_code=202)
@translate_app_errors
def declare_instance_model_state(
    instance_id: int,
    request: DeclareModelState,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelState:
    return model_state.change(session, claims, instance_id, action="declare", release_digest=request.release_digest)


@router.post("/{instance_id}/model-state/interactive", response_model=ModelState, status_code=202)
@translate_app_errors
def restore_instance_model_state(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelState:
    return model_state.change(session, claims, instance_id, action="restore")


@router.put("/{instance_id}/model-state/replicas", response_model=ModelState, status_code=202)
@translate_app_errors
def scale_instance_model_state(
    instance_id: int,
    request: ScaleModelState,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelState:
    return model_state.change(session, claims, instance_id, action="scale", replicas=request.replicas)
