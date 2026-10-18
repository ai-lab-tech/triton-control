"""Authorized asynchronous model performance endpoints."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.perf_jobs import ModelPerfRunResponse, ModelPerfStatusResponse, StartModelPerfRequest
from app.services.perf_analyzer import jobs

router = APIRouter(prefix="/api/instances/{instance_id}/models/{model_name}/perf", tags=["perf-analyzers"])


@router.post("/runs", response_model=ModelPerfRunResponse, status_code=202, operation_id="startModelPerf")
@translate_app_errors
def start_model_perf(
    instance_id: int, model_name: str, request: StartModelPerfRequest,
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> ModelPerfRunResponse:
    return jobs.start(session, claims, instance_id, model_name, request)


@router.get("/status", response_model=ModelPerfStatusResponse, operation_id="getModelPerfStatus")
@translate_app_errors
def get_model_perf_status(
    instance_id: int, model_name: str, model_version: str,
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> ModelPerfStatusResponse:
    return jobs.status(session, claims, instance_id, model_name, model_version)


@router.get("/runs/{run_id}", response_model=ModelPerfRunResponse, operation_id="getModelPerfRun")
@translate_app_errors
def get_model_perf_run(
    instance_id: int, model_name: str, run_id: str,
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> ModelPerfRunResponse:
    return jobs.get_run(session, claims, instance_id, model_name, run_id)


@router.post("/runs/{run_id}/stop", response_model=ModelPerfRunResponse, operation_id="stopModelPerf")
@translate_app_errors
def stop_model_perf(
    instance_id: int, model_name: str, run_id: str,
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> ModelPerfRunResponse:
    return jobs.stop(session, claims, instance_id, model_name, run_id)
