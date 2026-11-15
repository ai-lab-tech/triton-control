"""Real PostgreSQL atomic deployment admission, competing workers and crash recovery."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError
from app.repositories import deployment_intents as intents
from app.repositories import operations as repo
from app.services.operations import ExecutionContext, OperationWorker
from tests import test_deployment_operations as shared

URL = os.getenv("OPERATIONS_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set OPERATIONS_TEST_DATABASE_URL for real PostgreSQL tests")
class DeploymentPostgresTests(shared.DeploymentOperationTests):
    def setUp(self):
        self.schema = "deployment_test_" + uuid4().hex
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

    def test_simultaneous_admission_and_workers_create_one_deployment(self):
        barrier = Barrier(4)
        def submit(_):
            barrier.wait(timeout=10)
            return self.submit().operation_id
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(4)))
        self.assertEqual(len(set(ids)), 1)
        self.provider.available = True
        barrier = Barrier(4)
        def work(_):
            barrier.wait(timeout=10)
            return OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(work, range(4)))
        self.step(ids[0])
        self.assertEqual(self.step(ids[0]).state, "succeeded")
        self.assertEqual(sum(r.get("kind") == "Deployment" for r in self.provider.resources.values()), 1)

    def test_crash_after_provider_success_before_completion_fences_old_worker(self):
        result = self.submit()
        with Session(self.engine) as session:
            old = repo.claim(session, "old-worker", [intents.KIND], 30)
        context = ExecutionContext(self.service, lambda: Session(self.engine), old)
        self.service.adapter(intents.KIND).reconcile(old, context)
        # Simulate process death between provider reconciliation and publishing completion.
        with Session(self.engine) as session:
            row = repo.get(session, old.id)
            row.lease_until = repo.now(session) - timedelta(seconds=1)
            session.add(row)
            session.commit()
        writes = self.provider.writes
        self.provider.available = True
        self.assertEqual(self.step(result.operation_id).state, "succeeded")
        self.assertEqual(self.provider.writes, writes)
        with self.assertRaises(ConflictError):
            self.service.adapter(intents.KIND).reconcile(old, context)
        with Session(self.engine) as session:
            with self.assertRaises(ConflictError):
                repo.complete(session, old.id, "old-worker", old.generation, "failed", {}, "stale", 1)

    def test_concurrent_conflicting_keys_and_target_reservations(self):
        barrier = Barrier(2)
        def submit(index):
            barrier.wait(timeout=10)
            try:
                return self.submit(request=self.request.model_copy(update={"image": f"image-{index}"})).operation_id
            except ConflictError:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, range(2)))
        self.assertEqual(results.count("conflict"), 1)
        with self.assertRaises(ConflictError):
            self.submit(key="other", claims={"email": "ben@test.invalid"})

    def test_concurrent_distinct_targets_preserve_assignments(self):
        from app.db.entities import UserEntity
        barrier = Barrier(2)
        def submit(index):
            barrier.wait(timeout=10)
            return self.submit(request=self.request.model_copy(update={"deployment_name": f"iris-{index}"}))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(submit, range(2)))
        with Session(self.engine) as session:
            self.assertEqual(set(session.get(UserEntity, 1).assigned_instances), {"iris-0", "iris-1"})

    def test_deletion_waits_for_inflight_write_then_prevents_recreation(self):
        from app.repositories import instances
        from app.services.deployment import operations as deploy
        result = self.submit()
        entered, release, deleting = Event(), Event(), Event()
        original = self.provider.ensure

        def blocked(manifest, *args):
            if manifest["kind"] == "Deployment":
                entered.set()
                self.assertTrue(release.wait(timeout=10))
            return original(manifest, *args)

        def remove():
            with Session(self.engine) as session:
                deleting.set()
                with self.assertRaises(ConflictError):
                    deploy.prepare_deletion(session, result.instance_id)

        with patch.object(self.provider, "ensure", blocked), ThreadPoolExecutor(max_workers=2) as pool:
            worker = pool.submit(OperationWorker(self.service, lambda: Session(self.engine)).run_once)
            self.assertTrue(entered.wait(timeout=10))
            deletion = pool.submit(remove)
            try:
                self.assertTrue(deleting.wait(timeout=10))
                self.assertFalse(deletion.done())
            finally:
                release.set()
            worker.result(timeout=10)
            deletion.result(timeout=10)
        # A worker that observed concurrent cancellation relinquishes on lease expiry.
        with Session(self.engine) as session:
            row = repo.get(session, result.operation_id)
            row.lease_until = repo.now(session) - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.step(result.operation_id)
        self.step(result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "cancelled")
        with Session(self.engine) as session:
            instances.delete(session, instances.find_by_id(session, result.instance_id))
        writes = self.provider.writes
        self.step(result.operation_id)
        self.assertEqual(self.provider.writes, writes)
        self.assertFalse(any(r.get("kind") == "Deployment" for r in self.provider.resources.values()))
