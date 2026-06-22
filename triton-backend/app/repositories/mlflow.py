"""Data-access helpers for the singleton ``MlflowEntity`` row."""

from sqlmodel import Session

from app.db.entities import MlflowEntity

MLFLOW_ID = 1


def get(session: Session) -> MlflowEntity | None:
    """Return singleton MLflow installation, if it exists."""
    return session.get(MlflowEntity, MLFLOW_ID)


def save(session: Session, entity: MlflowEntity, *, refresh: bool = True) -> MlflowEntity:
    """Persist singleton MLflow installation record."""
    session.add(entity)
    session.commit()
    if refresh:
        session.refresh(entity)
    return entity


def delete(session: Session, entity: MlflowEntity) -> None:
    """Delete singleton MLflow installation record."""
    session.delete(entity)
    session.commit()
