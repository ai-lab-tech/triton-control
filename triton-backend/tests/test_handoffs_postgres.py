"""Real PostgreSQL receipt deduplication and atomic notification delivery."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, create_engine, select

from app.db.entities import HandoffEntity, HandoffNotificationEntity, PreparationTaskEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.services import handoff_sources, handoffs
from tests import test_handoffs as fixtures

TEST_URL = os.getenv("PREPARATION_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for real PostgreSQL tests")
class HandoffPostgresTests(fixtures.HandoffTests):
    def setUp(self):
        self.schema = "handoff_test_" + uuid4().hex
        self.admin_engine = create_engine(TEST_URL)
        with self.admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(TEST_URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.seed()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def race(self, changed):
        barrier = Barrier(2)

        def resolve(session, source):
            barrier.wait(timeout=15)
            return self.resolved(session, source)

        def submit(number):
            request = self.request(title="Changed" if changed and number else "Prepare Iris")
            with Session(self.engine) as session:
                try:
                    return handoffs.submit(session, self.submitter, request).id
                except ConflictError:
                    return "conflict"

        with patch.object(handoff_sources, "resolve", side_effect=resolve), ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(submit, [0, 1]))

    def test_simultaneous_identical_submissions_create_one_task_and_notification_per_recipient(self):
        results = self.race(False)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(HandoffNotificationEntity)).all()), 2)

    def test_simultaneous_different_payloads_have_one_immutable_winner(self):
        results = self.race(True)
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)

    def test_receipt_is_append_only_and_repeated_schema_upgrade_preserves_it(self):
        receipt = self.submit()
        self.session.commit()
        create_tables(self.engine)
        self.assertEqual(handoffs.get(self.session, self.submitter, receipt.id).id, receipt.id)
        for action in (
            "DELETE FROM preparation_handoffs",
            "UPDATE preparation_handoffs SET request_digest = 'changed'",
        ):
            with self.assertRaises(DBAPIError):
                self.session.execute(text(action))
                self.session.commit()
            self.session.rollback()
