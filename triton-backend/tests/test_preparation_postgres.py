"""Real PostgreSQL behavior and concurrency; each test uses an isolated schema."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.database import init_db
from app.db.entities import UserEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError
from app.schemas.preparation import TaskRevision
from app.services import preparation, teams
from tests import test_preparation as fixtures

TEST_URL = os.getenv("PREPARATION_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for real PostgreSQL tests")
class PreparationPostgresTests(fixtures.PreparationTests):
    """Run the behavior suite on PostgreSQL too, plus real independent-session races."""

    def setUp(self):
        self.schema = "preparation_test_" + uuid4().hex
        self.admin_engine = create_engine(TEST_URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(TEST_URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.seed()

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def test_simultaneous_claims_have_exactly_one_winner_and_one_claim_event(self):
        task = self.create(["test_data"])
        barrier = Barrier(2)

        def claim(user_id):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    preparation.claim(session, self.claims(user_id), task.id, TaskRevision(expected_revision=1))
                    return user_id
                except ConflictError:
                    session.rollback()
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [2, 3]))
        self.assertEqual(results.count("conflict"), 1)
        winner = next(result for result in results if result != "conflict")
        current = preparation.get_task(self.session, self.admin, task.id)
        self.assertEqual((current.assignee_user_id, current.revision), (winner, 2))
        history = preparation.list_events(self.session, self.admin, task.id)
        self.assertEqual([e.action for e in history], ["claimed", "created"])
        self.assertEqual(history[0].actor_user_id, winner)

    def test_stale_session_cannot_overwrite_newer_claim(self):
        task = self.create()
        with Session(self.engine) as stale:
            preparation.get_task(stale, self.claims(3), task.id)
            self.claim(task)
            with self.assertRaises(ConflictError):
                preparation.claim(stale, self.claims(3), task.id, TaskRevision(expected_revision=1))
            stale.rollback()
        self.assertEqual(preparation.get_task(self.session, self.admin, task.id).assignee_user_id, 2)

    def test_revocation_race_has_no_unauthorized_follow_up_and_keeps_truthful_history(self):
        task = self.create()
        barrier = Barrier(2)

        def change(revoke):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                if revoke:
                    teams.set_member(session, self.admin, self.team.id, 2, active=False)
                    return "revoked"
                try:
                    preparation.claim(session, self.engineer, task.id, TaskRevision(expected_revision=1))
                    return "claimed"
                except ForbiddenError:
                    session.rollback()
                    return "denied"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, [True, False]))
        self.assertIn("revoked", results)
        self.assertTrue("claimed" in results or "denied" in results)
        with Session(self.engine) as session:
            with self.assertRaises(ForbiddenError):
                preparation.get_task(session, self.engineer, task.id)
            session.rollback()
            current = preparation.get_task(session, self.admin, task.id)
            if "claimed" in results:
                self.assertIn("assignee_ineligible", current.blocked_reasons)
                self.assertEqual(current.revision, 2)
            else:
                self.assertEqual(current.revision, 1)

    def test_failed_event_write_rolls_back_claim_and_task_revision(self):
        task = self.create()
        with patch("app.services.preparation.repository.add_event", side_effect=RuntimeError("Storage failure")):
            with self.assertRaises(RuntimeError):
                self.claim(task)
        self.session.rollback()
        current = preparation.get_task(self.session, self.admin, task.id)
        self.assertEqual((current.revision, current.state, current.assignee_user_id), (1, "queued", None))
        self.assertEqual(len(preparation.list_events(self.session, self.admin, task.id)), 1)
        self.assertEqual(self.claim(task).revision, 2)

    def test_concurrent_upgrade_preserves_tasks_attempts_and_events(self):
        task = self.claim(self.create())
        preparation.record_attempt(
            self.session, self.engineer, task.id, TaskRevision(expected_revision=2), self.attempt()
        )
        # Release this session's read transaction before concurrent DDL.
        self.session.rollback()
        barrier = Barrier(2)

        def startup(_):
            barrier.wait(timeout=10)
            init_db()

        with patch("app.db.database.engine", self.engine), ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(startup, range(2)))
            init_db()
        current = preparation.get_task(self.session, self.admin, task.id)
        self.assertEqual((current.revision, current.assignee_user_id), (3, 2))
        self.assertEqual(len(preparation.list_attempts(self.session, self.admin, task.id)), 1)
        self.assertEqual(len(preparation.list_events(self.session, self.admin, task.id)), 3)
        self.assertEqual(self.session.get(UserEntity, 2).role, "member")
