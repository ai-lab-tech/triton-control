"""Actual PostgreSQL lease fencing and atomic receipt publication."""

import unittest

from tests import test_handoff_capture as capture
from tests import test_handoffs_postgres as postgres


@unittest.skipUnless(postgres.TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for PostgreSQL")
class CapturePostgresTests(capture.CaptureTests):
    cleanup_schema = postgres.HandoffPostgresTests.cleanup_schema

    def setUp(self):
        postgres.HandoffPostgresTests.setUp(self)
        self.configure_capture(clock=False)
