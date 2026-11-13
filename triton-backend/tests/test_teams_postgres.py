"""Real PostgreSQL races; opt in with TEAMS_TEST_DATABASE_URL (isolated schemas)."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from app.db.database import init_db
from app.db.entities import UserEntity, UserTeamEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import teams as repository
from app.schemas.teams import TeamRequest
from app.services import teams

TEST_URL = os.getenv("TEAMS_TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_URL, "Set TEAMS_TEST_DATABASE_URL to run PostgreSQL concurrency tests")
class TeamPostgresTests(unittest.TestCase):
    def setUp(self):
        self.schema = "teams_test_" + uuid4().hex
        self.admin_engine = create_engine(TEST_URL)
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(TEST_URL, connect_args={"options": f"-csearch_path={self.schema}"})
        self.addCleanup(self.cleanup_schema)
        self.claims = {"role": "admin", "email": "admin@test.invalid"}

    def cleanup_schema(self):
        self.engine.dispose()
        with self.admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.admin_engine.dispose()

    def seed(self):
        create_tables(self.engine)
        with Session(self.engine) as session:
            session.add_all([
                UserEntity(id=1, name="Admin", email="admin@test.invalid", role="admin", password_hash="hash"),
                UserEntity(id=2, name="Engineer", email="engineer@test.invalid", role="member", password_hash="hash"),
            ])
            session.commit()

    def test_concurrent_full_startup_and_repeat_upgrade_preserve_existing_users(self):
        # Arrange: emulate a pre-team installation with existing user data.
        UserEntity.__table__.create(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=1, name="Existing", email="existing@test.invalid", password_hash="hash"))
            session.commit()
        barrier = Barrier(3)

        def startup(_):
            barrier.wait(timeout=10)
            init_db()

        # Act
        with patch("app.db.database.engine", self.engine), ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(startup, range(3)))
            init_db()
        # Assert
        with Session(self.engine) as session:
            self.assertEqual(session.get(UserEntity, 1).name, "Existing")
            self.assertEqual(repository.list_all(session), [])
            self.assertEqual(session.exec(select(UserTeamEntity)).all(), [])

    def test_concurrent_name_creation_produces_one_team_and_one_event(self):
        # Arrange
        self.seed()
        barrier = Barrier(2)

        def create(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    teams.create_team(session, self.claims, TeamRequest(name="engineering"))
                    return "created"
                except ConflictError:
                    return "conflict"

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        # Assert
        self.assertCountEqual(results, ["created", "conflict"])
        with Session(self.engine) as session:
            rows = repository.list_all(session)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(repository.list_events(session, rows[0].id, 100)), 1)

    def test_concurrent_member_addition_has_one_pair_and_one_history_event(self):
        # Arrange
        self.seed()
        with Session(self.engine) as session:
            team = teams.create_team(session, self.claims, TeamRequest(name="engineering"))
        barrier = Barrier(3)

        def add(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return teams.set_member(session, self.claims, team.id, 2, active=True)

        # Act
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(add, range(3)))
        # Assert
        self.assertTrue(all(len(result) == 1 and result[0].eligible for result in results))
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(UserTeamEntity)).all()), 1)
            self.assertEqual([e.action for e in repository.list_events(session, team.id, 100)],
                             ["member_added", "team_created"])

    def test_deactivation_racing_with_addition_cannot_leave_eligible_members(self):
        # Arrange
        self.seed()
        with Session(self.engine) as session:
            team = teams.create_team(session, self.claims, TeamRequest(name="engineering"))
        barrier = Barrier(2)

        def change(deactivate):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                if deactivate:
                    teams.update_team(session, self.claims, team.id, TeamRequest(name=team.name, is_active=False))
                    return "inactive"
                try:
                    teams.set_member(session, self.claims, team.id, 2, active=True)
                    return "added"
                except ConflictError:
                    return "conflict"

        # Act
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, [True, False]))
        # Assert
        self.assertIn("inactive", results)
        with Session(self.engine) as session:
            self.assertFalse(repository.get(session, team.id).is_active)
            self.assertEqual(repository.eligible_user_ids(session, team.id), [])
