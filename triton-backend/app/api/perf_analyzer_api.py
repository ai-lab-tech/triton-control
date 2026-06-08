"""HTTP endpoints for installing Perf Analyzer support on Kubernetes.

Mounts a router at ``/api/perf-analyzers`` and delegates install work to the
Perf Analyzer service layer. Domain errors are translated at the HTTP boundary.
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    InstallPerfAnalyzerRequest,
    PerfAnalyzerDeleteResponse,
    PerfAnalyzerInstallResponse,
    PerfAnalyzerLatestRunResponse,
    PerfAnalyzerRunResponse,
    PerfAnalyzerStatusResponse,
    RunPerfAnalyzerRequest,
)
from app.services.perf_analyzer import installer

router = APIRouter(prefix="/api/perf-analyzers", tags=["perf-analyzers"])


@router.post("", response_model=PerfAnalyzerInstallResponse)
@translate_app_errors
def install_perf_analyzer(
    request: InstallPerfAnalyzerRequest,
    session: Session = Depends(get_session),
    _claims: dict[str, Any] = Depends(get_claims),
) -> PerfAnalyzerInstallResponse:
    """Install a Perf Analyzer SDK container on Kubernetes."""
    return installer.install_perf_analyzer(request, session)


@router.get("", response_model=PerfAnalyzerStatusResponse)
@translate_app_errors
def get_perf_analyzer_status(
    session: Session = Depends(get_session),
    _claims: dict[str, Any] = Depends(get_claims),
) -> PerfAnalyzerStatusResponse:
    """Return the singleton Perf Analyzer installation state."""
    return installer.get_perf_analyzer_status(session)


@router.delete("", response_model=PerfAnalyzerDeleteResponse)
@translate_app_errors
def uninstall_perf_analyzer(
    session: Session = Depends(get_session),
    _claims: dict[str, Any] = Depends(get_claims),
) -> PerfAnalyzerDeleteResponse:
    """Uninstall the singleton Perf Analyzer Kubernetes workload."""
    return installer.uninstall_perf_analyzer(session)


@router.post("/runs", response_model=PerfAnalyzerRunResponse)
@translate_app_errors
def run_perf_analyzer(
    request: RunPerfAnalyzerRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> PerfAnalyzerRunResponse:
    """Execute Perf Analyzer for one Triton instance model."""
    return installer.run_perf_analyzer(request, session, claims)


@router.get("/runs/latest", response_model=PerfAnalyzerLatestRunResponse)
@translate_app_errors
def get_latest_perf_analyzer_run(
    instance_id: int,
    model_name: str,
    model_version: str,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> PerfAnalyzerLatestRunResponse:
    """Return the latest persisted Perf Analyzer result for one model target."""
    request = RunPerfAnalyzerRequest(
        instance_id=instance_id,
        model_name=model_name,
        model_version=model_version,
    )
    return installer.get_latest_perf_analyzer_run(request, session, claims)
