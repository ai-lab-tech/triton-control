"""Real PostgreSQL task workspace retry races and additive schema behavior."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from app.db.entities import CodeServerEntity, TaskWorkspaceEntity
from app.db.schema import create_tables
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import task_workspaces
from tests import test_task_workspaces as fixtures

URL = os.getenv("PREPARATION_TEST_DATABASE_URL", "")


@unittest.skipUnless(URL, "Set PREPARATION_TEST_DATABASE_URL for PostgreSQL")
class TaskWorkspacePostgresTests(fixtures.TaskWorkspaceTests):
    def setUp(self):
        self.schema = "task_workspace_" + uuid4().hex
        self.admin_engine = create_engine(URL)
        with self.admin_engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_engine(URL, connect_args={"options": f"-csearch_path={self.schema}"})
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

    def test_simultaneous_open_preserves_one_association_workload_and_intent(self):
        barrier = Barrier(2)
        task_id = self.task.id
        def run(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return task_workspaces.open_workspace(session, self.engineer, task_id,
                                                      OpenTaskWorkspace()).workspace.id
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.session.exec(select(TaskWorkspaceEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(CodeServerEntity)).all()), 1)
        self.apply.assert_called_once()

    def test_concurrent_schema_upgrade_retains_existing_association(self):
        result = self.open()
        barrier = Barrier(2)
        def upgrade(_):
            barrier.wait(timeout=10)
            create_tables(self.engine)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(upgrade, range(2)))
        self.assertEqual(self.open().workspace.id, result.workspace.id)
