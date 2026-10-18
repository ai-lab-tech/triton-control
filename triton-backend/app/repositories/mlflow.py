"""Data-access helpers for the singleton ``MlflowEntity`` row."""

from sqlalchemy import text
from sqlmodel import Session

from app.db.entities import MlflowEntity
from app.exceptions import BadRequestError

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


def prepare_backend_store(session: Session) -> str:
    """Reuse PostgreSQL in an isolated schema, including a separate Alembic table."""
    url = session.connection().engine.url
    if url.get_backend_name() != "postgresql":
        raise BadRequestError("Managed MLflow requires a PostgreSQL database")
    session.execute(text("CREATE SCHEMA IF NOT EXISTS mlflow_tracking"))
    session.commit()
    existing_options = url.query.get("options", "")
    if isinstance(existing_options, tuple):
        existing_options = " ".join(existing_options)
    options = (existing_options + " -csearch_path=mlflow_tracking").strip()
    return url.set(drivername="postgresql+psycopg2").update_query_dict(
        {"options": options}
    ).render_as_string(hide_password=False)
