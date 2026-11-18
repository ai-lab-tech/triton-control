"""Real PostgreSQL publication, retry/crash recovery and reservation races."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from app.db.entities import PreparationSnapshotEntity, PreparationTaskEntity
from app.db.schema import create_tables
from app.repositories import operations as operation_repo
from app.services.operations import OperationWorker
from app.services.snapshots import service
from tests import test_experiment_snapshots as shared


@unittest.skipUnless(os.getenv("SNAPSHOTS_TEST_DATABASE_URL"), "Set isolated PostgreSQL test URL")
class SnapshotPostgresTests(shared.SnapshotTests):
    def setUp(self):
        schema = "snapshots_" + uuid4().hex
        admin = create_engine(os.environ["SNAPSHOTS_TEST_DATABASE_URL"])
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        self.engine = create_engine(os.environ["SNAPSHOTS_TEST_DATABASE_URL"],
                                    connect_args={"options": f"-csearch_path={schema}"})
        def cleanup():
            self.engine.dispose()
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.seed()
        self.setup_snapshots()

    def test_concurrent_submissions_and_workers_publish_one_task(self):
        barrier = Barrier(3)
        def submit(_):
            barrier.wait(timeout=10)
            return self.submit().operation.id
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids = list(pool.map(submit, range(3)))
        self.assertEqual(len(set(ids)), 1)
        def execute(_):
            return OperationWorker(service.operations, lambda: Session(self.engine)).run_once()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(execute, range(3)))
        self.assertEqual(self.read_operation(ids[0]).state, "succeeded")
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(PreparationSnapshotEntity)).all()), 1)

    def test_crash_before_commit_rolls_back_task_then_recovery_reuses_operation(self):
        accepted = self.submit()
        with patch.object(service.repository, "add", side_effect=RuntimeError("synthetic interrupted publication")):
            self.worker.run_once()
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])
        with Session(self.engine) as session:
            row = operation_repo.get(session, accepted.operation.id)
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.worker.run_once()
        self.assertEqual(self.receipt(accepted.operation.id).operation_id, accepted.operation.id)
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)

    def test_stale_generation_cannot_publish_after_new_worker_claim(self):
        accepted = self.submit()
        with Session(self.engine) as session:
            old = operation_repo.claim(session, "old-worker", [service.KIND], 30)
        with Session(self.engine) as session:
            row = operation_repo.get(session, old.id)
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        with Session(self.engine) as session:
            operation_repo.claim(session, "new-worker", [service.KIND], 30)
        from app.exceptions import ConflictError
        from app.services.operations import ExecutionContext
        with self.assertRaises(ConflictError):
            service.capture(old, ExecutionContext(service.operations, lambda: Session(self.engine), old))
        self.assertEqual(self.session.exec(select(PreparationSnapshotEntity)).all(), [])
        self.assertEqual(accepted.operation.id, old.id)

    def test_repeated_startup_keeps_completed_receipts(self):
        captured = self.finish()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: create_tables(self.engine), range(2)))
        self.assertEqual(self.receipt(captured.operation_id).manifest_digest, captured.manifest_digest)
