"""Run UI authorization/retry contracts against isolated real PostgreSQL schemas."""

import os
import unittest

from tests import test_build_ui_access as ui
from tests import test_builds_postgres as postgres


@unittest.skipUnless(os.getenv("BUILD_TEST_DATABASE_URL"), "Opt-in PostgreSQL build UI tests")
class BuildUiPostgresTests(ui.BuildUiTests):
    setUp = postgres.BuildPostgresTests.setUp
    cleanup_schema = postgres.BuildPostgresTests.cleanup_schema
