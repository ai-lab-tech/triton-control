"""Existing S3 profile tables acquire global uniqueness without data changes."""

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.s3_credentials import decrypt_secret, migrate_legacy_credentials
from app.db.database import _migrate_s3_profile_name_uniqueness


class S3ProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE s3_profiles (
                    id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL, name VARCHAR NOT NULL,
                    secret_key_enc VARCHAR NOT NULL,
                    CONSTRAINT uq_s3_profile_owner_name UNIQUE (owner_user_id, name)
                )
            """))
            conn.execute(text("""
                INSERT INTO s3_profiles VALUES
                (1, 1, 'dev', 'encrypted-one'), (2, 2, 'test', 'encrypted-two')
            """))

    def migrate(self):
        with patch("app.db.database.engine", self.engine):
            _migrate_s3_profile_name_uniqueness()

    def rows(self):
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT * FROM s3_profiles ORDER BY id")).all()

    def test_credentials_are_migrated_once_and_missing_key_can_be_retried(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE triton_instances (id INTEGER PRIMARY KEY, s3_secret_key_enc VARCHAR)"))
            conn.execute(text("INSERT INTO triton_instances VALUES (1, 'old-instance-secret'), (2, NULL)"))
        with patch.dict("os.environ", {"S3_SECRET_ENCRYPTION_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "S3_SECRET_ENCRYPTION_KEY"):
                migrate_legacy_credentials(self.engine)
        with patch.dict("os.environ", {"S3_SECRET_ENCRYPTION_KEY": "stable-test-key"}):
            migrate_legacy_credentials(self.engine)
            first = self.rows()
            migrate_legacy_credentials(self.engine)
            self.assertEqual(first, self.rows())
            self.assertNotEqual(first[0].secret_key_enc, "encrypted-one")
            self.assertEqual(decrypt_secret(first[0].secret_key_enc), "encrypted-one")
            with self.engine.connect() as conn:
                values = conn.execute(text("SELECT s3_secret_key_enc FROM triton_instances ORDER BY id")).scalars().all()
            self.assertEqual(decrypt_secret(values[0]), "old-instance-secret")
            self.assertIsNone(values[1])

    def test_restored_database_requires_original_key_at_startup(self):
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE triton_instances (id INTEGER PRIMARY KEY, s3_secret_key_enc VARCHAR)"))
            conn.execute(text("INSERT INTO triton_instances VALUES (1, 'instance-secret')"))
        with patch.dict("os.environ", {"S3_SECRET_ENCRYPTION_KEY": "original"}):
            migrate_legacy_credentials(self.engine)
        for table in ("s3_profiles", "triton_instances"):
            with self.subTest(table=table):
                for key in ("replacement", ""):
                    with patch.dict("os.environ", {"S3_SECRET_ENCRYPTION_KEY": key}):
                        with self.assertRaisesRegex(RuntimeError, "Restore the original"):
                            migrate_legacy_credentials(self.engine)
                with patch.dict("os.environ", {"S3_SECRET_ENCRYPTION_KEY": "original"}):
                    migrate_legacy_credentials(self.engine)
                if table == "s3_profiles":
                    with self.engine.begin() as conn:
                        conn.execute(text("DELETE FROM s3_profiles"))

    def test_existing_table_gains_global_uniqueness_and_preserves_data(self):
        # Arrange
        original = self.rows()
        # Act
        self.migrate()
        # Assert
        self.assertEqual(self.rows(), original)
        with self.assertRaises(IntegrityError), self.engine.begin() as conn:
            conn.execute(text("INSERT INTO s3_profiles VALUES (3, 3, 'dev', 'encrypted-three')"))

    def test_repeated_startup_is_idempotent(self):
        # Arrange
        self.migrate()
        original = self.rows()
        # Act
        self.migrate()
        # Assert
        self.assertEqual(self.rows(), original)
        indexes = inspect(self.engine).get_indexes("s3_profiles")
        self.assertEqual([index["name"] for index in indexes], ["uq_s3_profile_name"])

    def test_duplicate_names_stop_migration_without_changing_data(self):
        # Arrange
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE s3_profiles SET name = 'dev' WHERE id = 2"))
        original = self.rows()
        # Act
        with self.assertRaisesRegex(RuntimeError, "duplicate names: 'dev'"):
            self.migrate()
        # Assert
        self.assertEqual(self.rows(), original)
        self.assertEqual(inspect(self.engine).get_indexes("s3_profiles"), [])

    def test_postgres_locks_before_validation_and_replaces_owner_constraint_last(self):
        # Arrange
        engine = MagicMock()
        conn = engine.begin.return_value.__enter__.return_value
        conn.dialect.name = "postgresql"
        conn.execute.return_value.scalars.return_value.all.return_value = []
        # Act
        with patch("app.db.database.engine", engine):
            _migrate_s3_profile_name_uniqueness()
        # Assert
        statements = [str(call.args[0]) for call in conn.execute.call_args_list]
        self.assertTrue(statements[0].startswith("LOCK TABLE"))
        self.assertTrue(statements[1].startswith("SELECT name"))
        self.assertTrue(statements[2].startswith("CREATE UNIQUE INDEX IF NOT EXISTS"))
        self.assertEqual(statements[3],
                         "ALTER TABLE s3_profiles DROP CONSTRAINT IF EXISTS uq_s3_profile_owner_name")
