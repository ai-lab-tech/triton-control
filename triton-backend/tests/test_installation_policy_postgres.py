"""Real PostgreSQL cutover inventory and rotation races, isolated by schema."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ForbiddenError
from app.repositories import local_bindings
from tests import test_installation_cutover as fixtures

URL = os.getenv("INSTALLATION_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set INSTALLATION_TEST_DATABASE_URL for real PostgreSQL tests")
class InstallationPostgresTests(fixtures.CutoverTests):
    def setUp(self):
        self.schema = "installation_test_" + uuid4().hex
        self.admin_engine = create_engine(URL)
        with self.admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.seed()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def test_concurrent_rotation_and_revocation_cannot_recreate_scope(self):
        # Arrange
        scope = {"repository": "https://git.test/source"}
        with Session(self.engine) as session:
            local_bindings.save_credential(session, "read", "git", scope, "old")
        barrier = Barrier(2)

        def rotate():
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    local_bindings.save_credential(session, "read", "git", scope, "new", require_existing=True)
                except ForbiddenError:
                    session.rollback()

        def revoke():
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                local_bindings.delete_credential(session, "read")

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(rotate), pool.submit(revoke)]
            for future in futures:
                future.result(timeout=15)
        # Assert
        with Session(self.engine) as session:
            self.assertIsNone(local_bindings.credential(session, "read"))
