"""Run queue API contracts using real PostgreSQL transactions in CI."""
import unittest
from tests.test_engineering_queue import EngineeringQueueTests
from tests.test_preparation_postgres import PreparationPostgresTests, TEST_URL


@unittest.skipUnless(TEST_URL, "Set PREPARATION_TEST_DATABASE_URL for PostgreSQL")
class EngineeringQueuePostgresTests(EngineeringQueueTests):
    setUp = PreparationPostgresTests.setUp
    cleanup_schema = PreparationPostgresTests.cleanup_schema
    test_simultaneous_claims_have_exactly_one_winner_and_one_claim_event = (
        PreparationPostgresTests.test_simultaneous_claims_have_exactly_one_winner_and_one_claim_event
    )
