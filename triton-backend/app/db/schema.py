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

        # The audit trail and completed attempt records cannot be rewritten by
        # application DML. Re-running startup leaves their contents untouched.
        if connection.dialect.name == "postgresql":
            connection.execute(text("""
                CREATE OR REPLACE FUNCTION reject_preparation_history_mutation()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'Preparation history is append-only';
                END;
                $$
            """))
            for table in (
                "preparation_task_events", "preparation_attempts", "preparation_handoffs", "candidate_imports",
            ):
                connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_history ON {table}"))
                connection.execute(text(f"""
                    CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION reject_preparation_history_mutation()
                """))
        elif connection.dialect.name == "sqlite":
            for table in (
                "preparation_task_events", "preparation_attempts", "preparation_handoffs", "candidate_imports",
            ):
                for action in ("UPDATE", "DELETE"):
                    connection.execute(text(f"""
                        CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'Preparation history is append-only'); END
                    """))
