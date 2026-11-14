"""Serialized, additive table creation for repeatable multi-worker startup."""

from sqlalchemy import Engine, text
from sqlmodel import SQLModel

# Stable application-wide key, not Python's process-randomized hash().
SCHEMA_LOCK_ID = 741293816


def create_tables(engine: Engine) -> None:
    """Serialize check-and-create DDL; PostgreSQL checkfirst alone races."""
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK_ID})
        SQLModel.metadata.create_all(connection)
