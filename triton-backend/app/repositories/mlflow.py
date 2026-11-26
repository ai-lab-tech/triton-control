"""Data-access helpers for the singleton ``MlflowEntity`` row."""

import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
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
    """Use the configured MLflow database or isolate tracking in the control database."""
    configured = os.environ.get("MLFLOW_BACKEND_STORE_URI")
    if configured is not None:
        try:
            external = make_url(configured)
        except (ArgumentError, ValueError):
            raise BadRequestError("Invalid MLflow PostgreSQL connection URL") from None
        if external.get_backend_name() != "postgresql" or not external.database:
            raise BadRequestError("MLflow requires a PostgreSQL URL with a database name")
        return external.set(drivername="postgresql+psycopg2").render_as_string(hide_password=False)

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
