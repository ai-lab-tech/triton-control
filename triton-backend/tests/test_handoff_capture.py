"""Durable handoff jobs: admission, progress, cancellation and atomic publication."""

import hashlib
import json
import unittest
from datetime import datetime
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.handoffs_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import HandoffEntity, OperationEntity, PreparationTaskEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import operations
from app.services import handoff_capture, handoff_sources
from app.services.operations import OperationService, OperationWorker
from tests.test_handoffs import HandoffFixture


class CaptureTests(HandoffFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.configure_capture()

    def configure_capture(self, *, clock=True):
        self.service = OperationService()
        self.service.register(handoff_capture.KIND, handoff_capture.adapter)
        self.enterContext(patch.object(handoff_capture, "service", self.service))
        if clock:
            self.enterContext(patch.object(operations, "now", lambda _: datetime.utcnow()))
        self.worker = OperationWorker(self.service, lambda: Session(self.engine))

    def capture(self):
        return handoff_capture.submit(self.session, self.submitter, self.request(), "capture-one")

    def resolve(self, session, source, **options):
        options["checkpoint"](5)
        return self.resolved(session, source)

    def reserve_legacy_capture(self):
        """Reserve the exact payload shape persisted before lineage support."""
        payload = self.request().model_dump(mode="json", exclude={"lineage"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return operations.reserve(self.session, OperationEntity(
            id="legacy-capture", actor_id=4, kind=handoff_capture.KIND,
            target=handoff_capture.TARGET, idempotency_key="capture-one", payload=payload,
            payload_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        ))

    def test_pre_lineage_capture_replay_preserves_operation_and_stored_payload(self):
        # Arrange
        original = self.reserve_legacy_capture()
        payload, payload_hash = dict(original.payload), original.payload_hash
        # Act
        repeated = self.capture()
        stored = self.session.get(OperationEntity, original.id)
        # Assert
        self.assertEqual(repeated.id, original.id)
        self.assertEqual(stored.payload, payload)
        self.assertEqual(stored.payload_hash, payload_hash)
        self.assertEqual(len(self.session.exec(select(OperationEntity)).all()), 1)

    def test_pre_lineage_capture_rejects_changed_input_on_replay(self):
        # Arrange
        self.reserve_legacy_capture()
        changed = self.request(title="Changed requirements")
        # Act / Assert
        with self.assertRaises(ConflictError):
            handoff_capture.submit(self.session, self.submitter, changed, "capture-one")

    def test_admission_is_fast_and_worker_publishes_once(self):
        # Arrange
        provider = self.enterContext(patch.object(handoff_sources, "resolve", side_effect=self.resolve))
        # Act
        queued = self.capture()
        reads_at_admission = provider.call_count
        self.worker.run_once()
        repeated = self.capture()
        self.worker.run_once()
        result = self.service.read(self.session, self.submitter, queued.id)
        # Assert
        self.assertEqual(reads_at_admission, 0)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.result["bytes_verified"], 5)
        self.assertEqual(repeated.id, queued.id)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)

    def test_blocking_provider_read_keeps_lease_across_multiple_renewals(self):
        from datetime import timedelta
        from threading import Event
        queued = self.capture()
        stamp = [datetime.utcnow()]
        renewed = Event()
        original = operations.heartbeat

        def heartbeat(*args, **kwargs):
            original(*args, **kwargs)
            renewed.set()

        def slow_resolve(session, source, **options):
            # No streaming callback runs while a provider read is blocked.
            for _ in range(3):
                renewed.clear()
                stamp[0] += timedelta(seconds=20)
                self.assertTrue(renewed.wait(2), "Lease was not renewed during the blocked read")
            options["checkpoint"](5)
            return self.resolved(session, source)

        with patch.object(operations, "now", lambda _: stamp[0]), \
                patch.object(operations, "heartbeat", side_effect=heartbeat), \
                patch.object(handoff_capture, "HEARTBEAT_SECONDS", 0.01), \
                patch.object(handoff_sources, "resolve", side_effect=slow_resolve):
            self.worker.run_once()
        result = self.service.read(self.session, self.submitter, queued.id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)
        self.assertEqual(self.session.get(OperationEntity, queued.id).attempts, 1)

    def test_heartbeat_failure_is_propagated_before_publication(self):
        from threading import Event
        from unittest.mock import MagicMock
        failed = Event()
        context = MagicMock()

        def revoked():
            failed.set()
            raise ForbiddenError("Access revoked")

        context.checkpoint.side_effect = revoked
        with patch.object(handoff_capture, "HEARTBEAT_SECONDS", 0.01):
            with handoff_capture.keep_lease(context) as finish:
                self.assertTrue(failed.wait(2))
                with self.assertRaises(ForbiddenError):
                    finish()

    def test_cancellation_before_work_has_no_receipt(self):
        # Arrange
        queued = self.capture()
        provider = self.enterContext(patch.object(handoff_sources, "resolve"))
        # Act
        self.service.cancel(self.session, self.submitter, queued.id)
        self.worker.run_once()
        result = self.service.read(self.session, self.submitter, queued.id)
        # Assert
        self.assertEqual(result.state, "cancelled")
        provider.assert_not_called()
        self.assertEqual(self.session.exec(select(HandoffEntity)).all(), [])

    def test_cancellation_during_hashing_fences_publication(self):
        # Arrange
        queued = self.capture()
        def cancelled(session, source, **options):
            self.service.cancel(session, self.submitter, queued.id)
            return self.resolved(session, source)
        self.enterContext(patch.object(handoff_sources, "resolve", side_effect=cancelled))
        # Act
        self.worker.run_once()
        # Assert
        self.assertEqual(self.session.exec(select(HandoffEntity)).all(), [])
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])

    def test_failed_capture_can_retry_same_job_after_limits_change(self):
        # Arrange
        queued = self.capture()
        with patch.object(handoff_sources, "resolve", side_effect=BadRequestError("Input exceeds configured limit")):
            self.worker.run_once()
        self.enterContext(patch.object(handoff_sources, "resolve", side_effect=self.resolve))
        # Act
        retried = self.service.retry(self.session, self.submitter, queued.id)
        self.worker.run_once()
        result = self.service.read(self.session, self.submitter, queued.id)
        # Assert
        self.assertEqual(retried.id, queued.id)
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)

    def test_other_member_cannot_read_progress(self):
        # Arrange
        queued = self.capture()
        # Act / Assert
        with self.assertRaises(ForbiddenError):
            self.service.read(self.session, self.engineer, queued.id)

    def test_api_returns_202_and_status_without_provider_reads(self):
        # Arrange
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.submitter
        provider = self.enterContext(patch.object(handoff_sources, "resolve"))
        client = self.enterContext(TestClient(app))
        # Act
        response = client.post("/api/preparation/handoff-captures", json=self.request().model_dump(mode="json"),
                               headers={"Idempotency-Key": "http-capture"})
        # Assert
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["state"], "pending")
        self.assertTrue(response.json()["status_url"].startswith("/api/operations/"))
        provider.assert_not_called()

    def test_separate_worker_only_claims_its_configured_kind(self):
        # Arrange
        self.capture()
        worker = OperationWorker(self.service, lambda: Session(self.engine), kinds=["unrelated.kind"])
        # Act
        worked = worker.run_once()
        # Assert
        self.assertFalse(worked)
        self.assertEqual(self.session.exec(select(OperationEntity)).one().state, "pending")
