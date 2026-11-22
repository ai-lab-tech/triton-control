"""Serialized, additive table creation for repeatable multi-worker startup."""

from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel

# Stable application-wide key, not Python's process-randomized hash().
SCHEMA_LOCK_ID = 741293816
APPEND_ONLY_TABLES = (
    "preparation_task_events", "preparation_attempts", "preparation_handoffs",
    "candidate_imports", "build_context_receipts", "workspace_snapshot_inputs",
    "preparation_snapshots", "release_source_events",
)


def create_tables(engine: Engine) -> None:
    """Serialize check-and-create DDL; PostgreSQL checkfirst alone races."""
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK_ID})
        SQLModel.metadata.create_all(connection)
        instance_columns = inspect(connection).get_columns("triton_instances")
        if not any(column["name"] == "model_state" for column in instance_columns):
            connection.execute(text("ALTER TABLE triton_instances ADD COLUMN model_state JSON"))

        columns = inspect(connection).get_columns("build_destination")
        if not any(column["name"] == "push_secret" for column in columns):
            connection.execute(text("ALTER TABLE build_destination ADD COLUMN push_secret VARCHAR"))

        if not any(column["name"] == "push_config_enc" for column in columns):
            connection.execute(text("ALTER TABLE build_destination ADD COLUMN push_config_enc TEXT"))

        if not any(column["name"] == "reuse_pull_credentials" for column in columns):
            connection.execute(text("ALTER TABLE build_destination ADD COLUMN reuse_pull_credentials "
                                    "BOOLEAN NOT NULL DEFAULT FALSE"))

        # The audit trail and completed attempt records cannot be rewritten by
        # application DML. Re-running startup leaves their contents untouched.
        if connection.dialect.name == "postgresql":
            connection.execute(text("""
                CREATE OR REPLACE FUNCTION reject_preparation_history_mutation()
                RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'History in % is append-only', TG_TABLE_NAME;
                END;
                $$
            """))
            for table in APPEND_ONLY_TABLES:
                connection.execute(text(f"DROP TRIGGER IF EXISTS immutable_history ON {table}"))
                connection.execute(text(f"""
                    CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH ROW EXECUTE FUNCTION reject_preparation_history_mutation()
                """))
        elif connection.dialect.name == "sqlite":
            for table in APPEND_ONLY_TABLES:
                for action in ("UPDATE", "DELETE"):
                    connection.execute(text(f"""
                        CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action.lower()}
                        BEFORE {action} ON {table}
                        BEGIN SELECT RAISE(ABORT, 'History in {table} is append-only'); END
                    """))
