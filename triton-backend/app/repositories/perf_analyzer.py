"""Data-access helpers for the singleton ``PerfAnalyzerEntity`` row.

Provides ``get`` for reading the sole installation record and ``save`` for
persisting the record after Kubernetes resources were applied.
"""

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, col, select

from app.db.entities import PerfAnalyzerEntity, PerfAnalyzerRunEntity

PERF_ANALYZER_ID = 1


def get(session: Session) -> PerfAnalyzerEntity | None:
    """Return the singleton Perf Analyzer installation, if it exists."""
    return session.get(PerfAnalyzerEntity, PERF_ANALYZER_ID)


def save(session: Session, entity: PerfAnalyzerEntity, *, refresh: bool = True) -> PerfAnalyzerEntity:
    """Persist the singleton Perf Analyzer installation record."""
    session.add(entity)
    session.commit()
    if refresh:
        session.refresh(entity)
    return entity


def delete(session: Session, entity: PerfAnalyzerEntity) -> None:
    """Delete the singleton Perf Analyzer installation record."""
    session.delete(entity)
    session.commit()


def get_latest_run(
    session: Session,
    *,
    instance_id: int,
    model_name: str,
    model_version: str,
) -> PerfAnalyzerRunEntity | None:
    """Return the latest persisted run for one instance/model/version target."""
    statement = select(PerfAnalyzerRunEntity).where(
        PerfAnalyzerRunEntity.instance_id == instance_id,
        PerfAnalyzerRunEntity.model_name == model_name,
        PerfAnalyzerRunEntity.model_version == model_version,
    )
    return session.exec(statement).first()


def save_latest_run(session: Session, entity: PerfAnalyzerRunEntity) -> PerfAnalyzerRunEntity:
    """Persist or update the latest run record for one target."""
    session.add(entity)
    session.commit()
    session.refresh(entity)
    return entity


def delete_runs_for_instance(session: Session, instance_id: int) -> None:
    """Delete persisted Perf Analyzer latest-run rows for one instance."""
    session.exec(sa_delete(PerfAnalyzerRunEntity).where(col(PerfAnalyzerRunEntity.instance_id) == instance_id))
    session.commit()
