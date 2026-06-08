"""Pure mapping helpers for Perf Analyzer installation DTOs."""

from app.db.entities import PerfAnalyzerEntity
from app.schemas import PerfAnalyzerInstallResponse


def perf_analyzer_entity_to_dto(entity: PerfAnalyzerEntity) -> PerfAnalyzerInstallResponse:
    """Map the singleton Perf Analyzer installation record to its public DTO."""
    return PerfAnalyzerInstallResponse(
        namespace=entity.namespace,
        deployment_name=entity.deployment_name,
        image=entity.image,
        applied_resources=entity.applied_resources,
    )
