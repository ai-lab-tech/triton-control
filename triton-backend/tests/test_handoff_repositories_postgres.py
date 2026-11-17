"""Run repository handoff contracts and concurrent capture against PostgreSQL."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from sqlmodel import Session, select

from app.db.entities import HandoffEntity, HandoffNotificationEntity
from app.services import handoff_repositories, handoffs
from tests import test_handoff_repositories as fixtures
from tests import test_handoffs_postgres as postgres


@unittest.skipUnless(postgres.TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for PostgreSQL")
class RepositoryPostgresTests(fixtures.RepositoryTests):
    cleanup_schema = postgres.HandoffPostgresTests.cleanup_schema

    def setUp(self):
        postgres.HandoffPostgresTests.setUp(self)
        self.configure_storage()

    def test_concurrent_submissions_share_the_first_committed_inventory(self):
        # Arrange
        request = self.repository_request()
        captured = handoff_repositories.resolve(self.session, request.source)
        barrier = Barrier(2)

        def resolve(session, source):
            barrier.wait(timeout=15)
            return captured

        def submit(_):
            with Session(self.engine) as session:
                return handoffs.submit(session, self.submitter, request)

        # Act
        with patch.object(handoff_repositories, "resolve", side_effect=resolve), ThreadPoolExecutor(2) as pool:
            receipts = list(pool.map(submit, range(2)))

        # Assert
        self.assertEqual(receipts[0].id, receipts[1].id)
        self.assertEqual(receipts[0].source_snapshot, receipts[1].source_snapshot)
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(HandoffNotificationEntity)).all()), 2)
