"""Access to saved legacy benchmark results."""

from typing import Any

from sqlmodel import Session

from app.repositories import perf_analyzer
from app.schemas import PerfAnalyzerLatestRunResponse, RunPerfAnalyzerRequest
from app.services.access import get_instance_or_404


def get_latest_perf_analyzer_run(
    request: RunPerfAnalyzerRequest,
    session: Session,
    claims: dict[str, Any],
) -> PerfAnalyzerLatestRunResponse:
    """Return the persisted latest Perf Analyzer result for one model target."""
    get_instance_or_404(session, request.instance_id, claims)
    entity = perf_analyzer.get_latest_run(
        session,
        instance_id=request.instance_id,
        model_name=request.model_name,
        model_version=request.model_version,
    )
    if entity is None:
        return PerfAnalyzerLatestRunResponse(found=False)
    return PerfAnalyzerLatestRunResponse(
        found=True,
        executed_at=entity.executed_at,
        batch_size=entity.batch_size,
        concurrency_range=entity.concurrency_range,
        measurement_request_count=entity.measurement_request_count,
        input_data=entity.input_data,
        command=entity.command,
        output=entity.output,
    )
