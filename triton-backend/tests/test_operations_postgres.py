"""Real PostgreSQL locking, restart, and repeatable schema tests in isolated schemas."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import OperationEntity
from app.db.schema import create_tables
from app.repositories import operations as repo
from tests import test_operations as shared

URL = os.getenv("OPERATIONS_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set OPERATIONS_TEST_DATABASE_URL for PostgreSQL integration tests")
class OperationPostgresTests(shared.OperationTests):
    def setUp(self):
        self.schema = "operations_test_" + uuid4().hex
        self.admin_engine = create_engine(URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.seed()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def test_concurrent_submissions_and_workers(self):
        barrier = Barrier(4)

        def submit(_):
            barrier.wait(timeout=10)
            return self.submit().id

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(4)))
        self.assertEqual(len(set(ids)), 1)
        barrier = Barrier(4)

        def claim(index):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return repo.claim(session, str(index), [self.request.kind], 30)

        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim, range(4)))
        self.assertEqual(sum(row is not None for row in claims), 1)

    def test_concurrent_repeatable_schema(self):
        # Arrange: include transitive dependents (build evidence -> attempts -> operations).
        # Metadata orders parents before children; drop_all reverses that order.
        tables = {OperationEntity.__table__}
        for table in SQLModel.metadata.sorted_tables:
            if any(fk.column.table in tables for fk in table.foreign_keys):
                tables.add(table)
        SQLModel.metadata.drop_all(self.engine, tables=list(tables))
        # Act: concurrent startup must recreate the tables and their constraints.
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: create_tables(self.engine), range(3)))
        operation = self.submit()
        create_tables(self.engine)
        # Assert: repeated startup preserves operations and recreates every foreign key.
        self.assertEqual(self.submit().id, operation.id)
        inspector = inspect(self.engine)
        for table in tables:
            with self.subTest(table=table.name):
                actual = {
                    (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                    for fk in inspector.get_foreign_keys(table.name)
                }
                expected = {
                    (tuple(fk.parent.name for fk in constraint.elements),
                     constraint.referred_table.name,
                     tuple(fk.column.name for fk in constraint.elements))
                    for constraint in table.foreign_key_constraints
                }
                self.assertEqual(actual, expected)
