"""Real PostgreSQL upload reservations, idempotency, recovery and schema upgrades."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import build_contexts as repo
from app.services.builds import contexts, contexts_cleanup
from tests import test_build_contexts as fixtures

URL = os.getenv("BUILD_CONTEXT_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set BUILD_CONTEXT_TEST_DATABASE_URL for PostgreSQL tests")
class BuildContextPostgresTests(fixtures.ContextTests):
    def setUp(self):
        self.schema = "build_context_" + uuid4().hex
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

    def test_simultaneous_submit_reserves_one_operation_and_context(self):
        barrier = Barrier(4)

        def submit(_):
            barrier.wait(timeout=10)
            return self.submit().id

        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(4)))
        self.assertEqual(len(set(ids)), 1)

    def test_simultaneous_uploads_have_one_winner(self):
        context_id = self.submit().id
        barrier = Barrier(4)

        def reserve(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    return contexts.begin_upload(session, self.claims, context_id)[0]
                except ConflictError:
                    return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            tokens = list(pool.map(reserve, range(4)))
        self.assertEqual(sum(token is not None for token in tokens), 1)

    def test_concurrent_retention_sweeps_keep_receipt(self):
        context_id = self.submit().id
        self.upload(context_id)
        self.run_worker(context_id)
        with Session(self.engine) as session:
            row = repo.get(session, context_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            row.upload_until = None
            session.add(row)
            session.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: contexts_cleanup.cleanup(lambda: Session(self.engine)), range(2)))
        self.assertEqual(sum(r["removed"] for r in results), 1)
        self.assertEqual(sum(r["failed"] for r in results), 0)
        with Session(self.engine) as session:
            self.assertIsNotNone(repo.receipt(session, context_id))

    def test_repeatable_upgrade_and_append_only_receipt(self):
        context_id = self.submit().id
        self.upload(context_id)
        self.run_worker(context_id)
        create_tables(self.engine)
        create_tables(self.engine)
        with Session(self.engine) as session:
            self.assertTrue(contexts.read(session, self.claims, context_id).ready)
            session.rollback()
            with self.assertRaises(DBAPIError):
                session.execute(text("UPDATE build_context_receipts SET payload='{}'"))
                session.commit()
            session.rollback()
            self.assertTrue(contexts.read(session, self.claims, context_id).ready)
