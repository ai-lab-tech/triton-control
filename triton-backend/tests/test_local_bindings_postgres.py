"""Real PostgreSQL policy races and additive upgrade verification."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import local_bindings as repository
from tests import test_local_bindings


@unittest.skipUnless(os.getenv("LOCAL_BINDINGS_TEST_DATABASE_URL"), "Set isolated PostgreSQL URL")
class PostgresLocalBindingTests(test_local_bindings.LocalBindingTests):
    def make_engine(self):
        url = os.environ["LOCAL_BINDINGS_TEST_DATABASE_URL"]
        schema = "binding_test_" + uuid4().hex
        admin_engine = create_engine(url)
        with admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))

        def cleanup():
            with admin_engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin_engine.dispose()

        self.addCleanup(cleanup)
        return create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def race(self, expected):
        barrier = Barrier(2)

        def attempt(index):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                try:
                    return repository.save_policy(session, expected, {"winner": index})
                except ConflictError:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertCountEqual(results, [expected + 1, "conflict"])
        self.assertEqual(repository.policy(self.session).revision, expected + 1)

    def test_concurrent_first_policy_has_one_winner(self):
        self.race(0)

    def test_concurrent_policy_updates_have_one_winner(self):
        self.configure()
        self.race(1)

    def test_repeated_startup_preserves_policy_profiles_and_credentials(self):
        self.configure()
        before = self.admit().model_dump()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: create_tables(self.engine), range(2)))
        self.assertEqual(self.admit().model_dump(), before)
        self.assertEqual(self.session.get(type(self.profiles[0]), self.profiles[0].id).bucket, "synthetic-metadata")
