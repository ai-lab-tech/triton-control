"""Validate simultaneous submissions and single worker ownership against PostgreSQL."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from sqlmodel import Session

from app.repositories import operations
from app.schemas.operations import OperationRequest
from app.services.candidate_validation.execution import ValidationAdapter
from app.services.operations import OperationService
from tests import test_candidate_validation as validation
from tests import test_preparation_postgres as postgres


@unittest.skipUnless(postgres.TEST_URL, "Requires disposable PostgreSQL")
class ValidationPostgresTests(validation.ValidationAccessTests):
    setUp = postgres.PreparationPostgresTests.setUp
    cleanup_schema = postgres.PreparationPostgresTests.cleanup_schema

    def test_concurrent_validation_replay_and_claim(self):
        task = self.claim(self.create())
        service = OperationService()
        service.register(operations.VALIDATION_KIND, ValidationAdapter())
        request = OperationRequest(
            kind=operations.VALIDATION_KIND, target=str(task.id), payload=validation.fixture("manifest")
        )
        barrier = Barrier(3)

        def submit(_):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return service.submit(session, self.engineer, request, "same-run").id

        def claim(worker):
            with Session(self.engine) as session:
                barrier.wait(timeout=10)
                return operations.claim(session, str(worker), [operations.VALIDATION_KIND], 30)

        with (
            patch.dict(os.environ, TRITON_VALIDATION_NAMESPACE="validation"),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            self.assertEqual(len(set(pool.map(submit, range(3)))), 1)
            self.assertEqual(sum(row is not None for row in pool.map(claim, range(3))), 1)
