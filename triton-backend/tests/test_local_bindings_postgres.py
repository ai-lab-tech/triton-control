"""Real PostgreSQL policy races and additive upgrade verification."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError
from app.repositories import local_bindings as repository
from app.schemas.local_bindings import RegistryCredential
from app.services import local_bindings as service
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

    def test_inflight_rotation_cannot_restore_scope_after_recreation(self):
        rotating = Event()
        replaced = Event()
        encrypt = service.encrypt_registry_secret
        original = RegistryCredential(
            provider="registry", registry="registry.example.invalid", repository_prefix="synthetic",
            username="test-user", password="inflight-rotation", permission="pull",
        )
        replacement = original.model_copy(update={"repository_prefix": "replacement"})
        # Use a different secret so the replacing request does not pause too.
        replacement = RegistryCredential.model_validate(replacement.model_dump() | {"password": "replacement-secret"})

        def pause_rotation(value):
            result = encrypt(value)
            if value == "inflight-rotation":
                rotating.set()
                if not replaced.wait(timeout=10):
                    raise TimeoutError("Credential recreation did not finish")
            return result

        def rotate():
            with Session(self.engine) as session:
                service.put_credential(session, self.admin_claims, "pull-credential", original)

        with patch.object(service, "encrypt_registry_secret", side_effect=pause_rotation):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(rotate)
                try:
                    self.assertTrue(rotating.wait(timeout=10))
                    service.delete_credential(self.session, self.admin_claims, "pull-credential")
                    service.put_credential(self.session, self.admin_claims, "pull-credential", replacement)
                finally:
                    replaced.set()

                with self.assertRaises(ForbiddenError):
                    future.result(timeout=10)

        row = repository.credential(self.session, "pull-credential")
        self.assertEqual(row.scope["repository_prefix"], "replacement")
        self.assertEqual(service.decrypt_registry_secret(row.secret_enc), "replacement-secret")
