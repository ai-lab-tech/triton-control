"""Real PostgreSQL publication races in isolated schemas (Arrange–Act–Assert)."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from app.db.entities import ServingCandidateEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.schemas.lineage import InventoryV1
from app.services.lineage import publish
from tests.test_lineage import candidate

TEST_URL = os.getenv("LINEAGE_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_URL, "Set LINEAGE_TEST_DATABASE_URL for PostgreSQL tests")
class LineagePostgresTests(unittest.TestCase):
    def setUp(self):
        self.schema = "lineage_test_" + uuid4().hex
        self.admin_engine = create_engine(TEST_URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(TEST_URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def race(self, operations):
        barrier = Barrier(len(operations))
        inventory = InventoryV1(files=())

        def worker(operation):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    identity = publish(session, {"role": "admin"},
                                       candidate(inventory, operation=operation), (inventory,))
                    session.commit()
                    return identity
                except ConflictError:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=len(operations)) as pool:
            return list(pool.map(worker, operations))

    def test_identical_concurrent_publication_has_one_record(self):
        # Arrange
        operations = ["packaging"] * 4
        # Act
        results = self.race(operations)
        # Assert
        self.assertEqual(len(set(results)), 1)
        self.assertNotIn("conflict", results)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(ServingCandidateEntity)).all()), 1)

    def test_conflicting_concurrent_publication_preserves_winner(self):
        # Arrange
        operations = ["packaging", "conversion"]
        # Act
        results = self.race(operations)
        # Assert
        self.assertEqual(results.count("conflict"), 1)
        with Session(self.engine) as session:
            rows = session.exec(select(ServingCandidateEntity)).all()
            self.assertEqual(len(rows), 1)
            self.assertIn(rows[0].digest, results)

    def test_rollback_does_not_publish_and_retry_recovers(self):
        # Arrange
        inventory = InventoryV1(files=())
        prepared = candidate(inventory)
        # Act
        with Session(self.engine) as session:
            publish(session, {"role": "admin"}, prepared, (inventory,))
            session.rollback()
        # Assert
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(ServingCandidateEntity)).all(), [])
            identity = publish(session, {"role": "admin"}, prepared, (inventory,))
            session.commit()
        create_tables(self.engine)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(ServingCandidateEntity)).one().digest, identity)
