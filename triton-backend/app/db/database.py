"""Database engine setup and session lifecycle management.

Provides:
  ``engine``            — the global SQLAlchemy/SQLModel engine, configured
                           from the ``DATABASE_URL`` environment variable
                           (defaults to a local PostgreSQL instance).
  ``get_session()``     — FastAPI dependency that yields a ``Session`` and
                           commits on success or rolls back on error.
  ``session_factory()`` — context-manager variant for background tasks that
                           run outside the FastAPI request lifecycle.
  ``init_db()``         — creates all tables that do not yet exist;
                           called once on application startup.

Notes:
  - SQL echo is disabled by default; set ``DATABASE_ECHO=1`` when debugging
    database statements.
  - ``pool_pre_ping=True`` recycles stale pooled connections automatically.
"""

import os
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import text
from sqlmodel import Session, create_engine

# Load variables from a local .env file if present.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


DATABASE_URL = os.getenv(
    "DATABASE_URL",

    # fallback default value
    "postgresql://triton:tritonpw@localhost:5433/triton_backend",
)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


engine = create_engine(
    DATABASE_URL,
    echo=_parse_bool(os.getenv("DATABASE_ECHO"), default=False),
    pool_pre_ping=True,  # Verify connections before using
)


def get_session() -> Generator[Session, None, None]:
    """
    Get database session for FastAPI dependency injection.

    Yields:
        Session: SQLModel database session
    """
    with Session(engine) as session:
        yield session


def session_factory() -> Session:
    """Return a plain Session bound to the application engine.

    Use this outside of FastAPI's dependency injection (background tasks,
    middleware).  The caller is responsible for closing the session::

        with session_factory() as session:
            ...
    """
    return Session(engine)


def init_db() -> None:
    """Initialize database - create all tables."""
    from sqlmodel import SQLModel

    from app.db.entities import (  # Import models to register them
        CodeServerEntity,
        DashboardAlertEntity,
        ErrorEventEntity,
        MlflowEntity,
        OidcConfigEntity,
        PerfAnalyzerEntity,
        PerfAnalyzerRunEntity,
        TritonInstanceEntity,
        UserEntity,
        WorkflowS3CredentialEntity,
    )

    SQLModel.metadata.create_all(engine)
    _migrate_triton_instances_table()
    _migrate_oidc_config_table()
    _migrate_perf_analyzer_table()
    _migrate_mlflow_table()


def _migrate_oidc_config_table() -> None:
    """Apply lightweight schema migration for oidc_config."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS oidc_config
                ADD COLUMN IF NOT EXISTS client_secret VARCHAR NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS oidc_config
                ADD COLUMN IF NOT EXISTS ca_certificate TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS oidc_config
                DROP COLUMN IF EXISTS post_logout_redirect_uri
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS oidc_config
                DROP COLUMN IF EXISTS silent_refresh_redirect_uri
                """
            )
        )


def _migrate_triton_instances_table() -> None:
    """Apply lightweight schema migration for triton_instances."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS server_metadata JSON
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS repository_models JSON NOT NULL DEFAULT '[]'
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS health_live BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS health_ready BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS health_last_checked_at TIMESTAMP
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS health_error VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS s3_ca_certificate TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS triton_verify_ssl BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS triton_ca_certificate TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_url VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_cpu DOUBLE PRECISION NOT NULL DEFAULT 0
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_ram DOUBLE PRECISION NOT NULL DEFAULT 0
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_gpu DOUBLE PRECISION NOT NULL DEFAULT 0
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_last_checked_at TIMESTAMP
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS metrics_error VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_runtime VARCHAR NOT NULL DEFAULT 'external'
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_namespace VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_name VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_service_name VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_secret_name VARCHAR
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS deployment_log TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS is_self_deployed BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS triton_instances
                ADD COLUMN IF NOT EXISTS pod_statuses JSON NOT NULL DEFAULT '[]'
                """
            )
        )


def _migrate_perf_analyzer_table() -> None:
    """Apply lightweight schema migration for perf_analyzer."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS perf_analyzer
                ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'creating'
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS perf_analyzer
                ADD COLUMN IF NOT EXISTS status_message TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS perf_analyzer
                ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMP NOT NULL DEFAULT NOW()
                """
            )
        )


def _migrate_mlflow_table() -> None:
    """Apply lightweight schema migration for mlflow singleton table."""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS mlflow
                ADD COLUMN IF NOT EXISTS status VARCHAR NOT NULL DEFAULT 'creating'
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS mlflow
                ADD COLUMN IF NOT EXISTS status_message TEXT NOT NULL DEFAULT ''
                """
            )
        )
        conn.execute(
            text(
                """
                ALTER TABLE IF EXISTS mlflow
                ADD COLUMN IF NOT EXISTS last_transition_at TIMESTAMP NOT NULL DEFAULT NOW()
                """
            )
        )
