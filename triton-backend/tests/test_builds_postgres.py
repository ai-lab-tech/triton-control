"""Build recovery and independent-session races on real PostgreSQL."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import builds, operations
from tests import test_builds as fixtures

URL = os.getenv("BUILD_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set BUILD_TEST_DATABASE_URL for PostgreSQL build tests")
class BuildPostgresTests(fixtures.BuildTests):
    def setUp(self):
        self.schema = "build_test_" + uuid4().hex
        self.admin_engine = create_engine(URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.seed()
        self.seed_builds()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def test_simultaneous_submissions_have_one_durable_intent(self):
        # Arrange
        barrier = Barrier(4)

        def submit(_):
            barrier.wait(timeout=10)
            return self.submit_build().id

        # Act
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(4)))
        # Assert
        self.assertEqual(len(set(ids)), 1)

    def test_only_one_worker_claims_build_and_expired_worker_is_fenced(self):
        # Arrange
        build_id = self.submit_build().id
        barrier = Barrier(4)

        def claim(number):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return operations.claim(session, str(number), [fixtures.execution.KIND], 30)

        # Act
        with ThreadPoolExecutor(max_workers=4) as pool:
            claimed = list(pool.map(claim, range(4)))
        winner = next(row for row in claimed if row is not None)
        # Assert
        self.assertEqual(sum(row is not None for row in claimed), 1)
        with Session(self.engine) as session:
            row = operations.get(session, build_id)
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
            replacement = operations.claim(session, "replacement", [fixtures.execution.KIND], 30)
            self.assertGreater(replacement.generation, winner.generation)
            with self.assertRaises(ConflictError):
                operations.owned(session, build_id, winner.worker, winner.generation)

    def test_concurrent_retention_sweeps_and_repeatable_upgrade(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.finish(build_id)
        create_tables(self.engine)
        create_tables(self.engine)
        barrier = Barrier(2)

        def sweep(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return builds.expire_logs(session, datetime.utcnow() + timedelta(days=31))

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(sweep, range(2)))
        # Assert
        self.assertEqual(sum(counts), 1)
        with Session(self.engine) as session:
            self.assertEqual(builds.latest(session, build_id).final_digest, fixtures.DIGEST)
