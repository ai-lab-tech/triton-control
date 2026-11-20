"""Real PostgreSQL serialization for a model moved between storage locations."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from sqlmodel import Session, select

from app.db.entities import HandoffEntity
from app.services import handoff_repositories, handoffs
from tests import test_control_lineage as fixtures
from tests import test_handoffs_postgres as postgres


@unittest.skipUnless(postgres.TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for real PostgreSQL tests")
class ControlLineagePostgresTests(fixtures.ControlLineageTests):
    setUp = postgres.HandoffPostgresTests.setUp
    cleanup_schema = postgres.HandoffPostgresTests.cleanup_schema

    def test_concurrent_storage_locations_publish_one_identity(self):
        barrier = Barrier(2)
        def resolve(session, source, **kwargs):
            result = self.repository(session, source)
            barrier.wait(timeout=15)
            return result
        def submit(path):
            with Session(self.engine) as session:
                return handoffs.submit(session, self.submitter, self.request_with_lineage(path)).id
        with patch.object(handoff_repositories, "resolve", side_effect=resolve), ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(submit, ["first", "relocated"]))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)
