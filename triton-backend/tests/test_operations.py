"""Lifecycle and HTTP tests; PostgreSQL races are in test_operations_postgres."""

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI, Response
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.operation_api import router, submit as submit_endpoint
from app.db.entities import OperationEntity, UserEntity
from app.exceptions import ConflictError, ForbiddenError, UnprocessableEntityError
from app.repositories import operations as repo
from app.schemas.operations import OperationRequest, ReconcileResult
from app.services.operations import OperationService, OperationWorker


class ExampleAdapter:
    """Simulates an external store, including a lost acknowledgement after a write."""

    def __init__(self):
        self.resources = {}
        self.writes = 0
        self.lose_ack = False
        self.allowed = True

    def normalize(self, payload):
        return payload

    def authorize(self, session, actor, target, action):
        if not self.allowed or actor.email != "anna@test.invalid":
            raise ForbiddenError("Target access denied")

    def reconcile(self, operation, context):
        context.checkpoint()
        if operation.cancel_requested:
            self.resources.pop(operation.id, None)
            return ReconcileResult(state="cancelled")
        if operation.id not in self.resources:
            self.resources[operation.id] = "external-resource"
            self.writes += 1
            if self.lose_ack:
                self.lose_ack = False
                raise TimeoutError("secret must not reach status")
        return ReconcileResult(state="succeeded", result={"resource": self.resources[operation.id]})


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        clock = patch("app.repositories.operations.now", lambda _: datetime.utcnow())
        clock.start()
        self.addCleanup(clock.stop)
        self.seed()

    def seed(self):
        with Session(self.engine) as session:
            session.add_all([
                UserEntity(id=1, email="anna@test.invalid", name="Anna", role="member"),
                UserEntity(id=2, email="ben@test.invalid", name="Ben", role="member"),
            ])
            session.commit()
        self.service = OperationService()
        self.adapter = ExampleAdapter()
        self.service.register("test.capture", self.adapter)
        self.claims = {"email": "anna@test.invalid", "role": "member"}
        self.request = OperationRequest(kind="test.capture", target="model-3", payload={"a": 1, "b": 2})

    def submit(self, key="request-1", request=None, claims=None):
        with Session(self.engine) as session:
            return self.service.submit(session, claims or self.claims, request or self.request, key)

    def due(self, operation_id):
        with Session(self.engine) as session:
            row = repo.get(session, operation_id)
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()

    def test_canonical_replay_conflict_and_scope(self):
        first = self.submit()
        replay = self.submit(request=self.request.model_copy(update={"payload": {"b": 2, "a": 1}}))
        self.assertEqual(first.id, replay.id)
        with self.assertRaises(ConflictError):
            self.submit(request=self.request.model_copy(update={"payload": {"a": 4}}))
        other = self.submit(request=self.request.model_copy(update={"target": "model-4"}))
        self.assertNotEqual(first.id, other.id)

    def test_unknown_kind_and_invalid_payload_fail_before_reservation(self):
        with self.assertRaises(UnprocessableEntityError):
            self.submit(request=self.request.model_copy(update={"kind": "shell"}))
        with self.assertRaises(UnprocessableEntityError):
            self.submit(request=self.request.model_copy(update={"payload": {"value": float("nan")}}))
        with self.assertRaises(UnprocessableEntityError):
            self.submit(key=" ")
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(OperationEntity)).all(), [])

    def test_provider_timeout_preserves_durable_reconciliation_result(self):
        operation = self.submit()
        with Session(self.engine) as session:
            row = repo.get(session, operation.id)
            row.result = {"jobUID": "already-recorded"}
            session.add(row)
            session.commit()
        with patch.object(self.adapter, "reconcile", side_effect=TimeoutError()):
            self.assertTrue(OperationWorker(self.service, lambda: Session(self.engine)).run_once())
        with Session(self.engine) as session:
            current = repo.get(session, operation.id)
            self.assertEqual(current.state, "pending")
            self.assertEqual(current.result, {"jobUID": "already-recorded"})

    def test_uncertain_write_recovered_by_new_worker_without_duplicate(self):
        operation = self.submit()
        self.adapter.lose_ack = True
        first = OperationWorker(self.service, lambda: Session(self.engine))
        self.assertTrue(first.run_once())
        with Session(self.engine) as session:
            row = repo.get(session, operation.id)
            self.assertEqual(row.state, "pending")
            self.assertNotIn("secret", row.message)
        self.due(operation.id)
        restarted = OperationWorker(self.service, lambda: Session(self.engine))
        self.assertTrue(restarted.run_once())
        self.assertFalse(restarted.run_once())
        self.assertEqual(self.adapter.writes, 1)
        with Session(self.engine) as session:
            self.assertEqual(repo.get(session, operation.id).state, "succeeded")

    def test_expired_worker_fenced_after_reclaim(self):
        operation = self.submit()
        with Session(self.engine) as session:
            old = repo.claim(session, "old", [self.request.kind], 30)
            row = repo.get(session, operation.id)
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        with Session(self.engine) as session:
            new = repo.claim(session, "new", [self.request.kind], 30)
            self.assertGreater(new.generation, old.generation)
        with Session(self.engine) as session:
            with self.assertRaises(ConflictError):
                repo.complete(session, operation.id, "old", old.generation, "succeeded", {}, "", 1)
        with Session(self.engine) as session:
            with self.assertRaises(ConflictError):
                repo.heartbeat(session, operation.id, "old", old.generation, 30)

    def test_cancel_requires_cleanup_and_retry_reuses_identity(self):
        operation = self.submit()
        self.adapter.resources[operation.id] = "external-resource"
        with Session(self.engine) as session:
            result = self.service.cancel(session, self.claims, operation.id)
            self.assertTrue(result.cancel_requested)
            self.assertNotEqual(result.state, "cancelled")
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        self.assertNotIn(operation.id, self.adapter.resources)
        with Session(self.engine) as session:
            self.assertEqual(repo.get(session, operation.id).state, "cancelled")
            with self.assertRaises(ConflictError):
                self.service.retry(session, self.claims, operation.id)
        other = self.submit(key="second")
        with Session(self.engine) as session:
            row = repo.get(session, other.id)
            row.state = "failed"
            session.add(row)
            session.commit()
            retried = self.service.retry(session, self.claims, other.id)
            self.assertEqual(retried.id, other.id)
            self.assertEqual(retried.state, "pending")

    def test_permissions_rechecked_for_read_mutation_and_execution(self):
        operation = self.submit()
        self.adapter.allowed = False
        with Session(self.engine) as session:
            for method in (self.service.read, self.service.cancel, self.service.retry):
                with self.assertRaises(ForbiddenError):
                    method(session, self.claims, operation.id)
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        self.assertEqual(self.adapter.writes, 0)
        self.adapter.allowed = True
        with Session(self.engine) as session:
            actor = session.get(UserEntity, 1)
            actor.role = "viewer"
            session.add(actor)
            session.commit()
        with self.assertRaises(ForbiddenError):
            self.submit(key="new")

    def test_http_contract_and_location(self):
        app = FastAPI()
        app.include_router(router)
        contract = app.openapi()["paths"]["/api/operations"]["post"]
        self.assertIn("202", contract["responses"])
        header = next(p for p in contract["parameters"] if p["name"] == "idempotency-key")
        self.assertTrue(header["required"])
        response = Response()
        with patch("app.api.operation_api.service", self.service), Session(self.engine) as session:
            result = submit_endpoint(self.request, response, "http", session, self.claims)
        self.assertEqual(response.headers["location"], result.status_url)
        with Session(self.engine) as session:
            self.assertEqual(repo.get(session, result.id).state, "pending")

    def test_cancellation_racing_with_completion_is_not_lost(self):
        operation = self.submit()
        with Session(self.engine) as session:
            claimed = repo.claim(session, "worker", [self.request.kind], 30)
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, operation.id)
        with Session(self.engine) as session:
            repo.complete(session, operation.id, "worker", claimed.generation, "succeeded", {}, "", 1)
            row = repo.get(session, operation.id)
            self.assertEqual(row.state, "pending")
            self.assertTrue(row.cancel_requested)

    def test_revocation_during_external_call_prevents_success(self):
        operation = self.submit()
        original = self.adapter.reconcile

        def revoke(row, context):
            result = original(row, context)
            self.adapter.allowed = False
            return result

        self.adapter.reconcile = revoke
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        with Session(self.engine) as session:
            self.assertEqual(repo.get(session, operation.id).state, "pending")
        self.assertEqual(self.adapter.writes, 1)

    def test_actor_scope_does_not_reuse_another_users_operation(self):
        # This adapter deliberately permits both users for this scope test.
        self.adapter.authorize = lambda *args: None
        anna = self.submit()
        ben = self.submit(claims={"email": "ben@test.invalid", "role": "member"})
        self.assertNotEqual(anna.id, ben.id)

    def test_process_death_after_external_write_recovers_after_lease_expiry(self):
        operation = self.submit()
        original = self.adapter.reconcile

        def crash(row, context):
            original(row, context)
            raise SystemExit("simulated process termination")

        self.adapter.reconcile = crash
        with self.assertRaises(SystemExit):
            OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        with Session(self.engine) as session:
            row = repo.get(session, operation.id)
            self.assertEqual(row.state, "running")
            row.lease_until = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.adapter.reconcile = original
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        self.assertEqual(self.adapter.writes, 1)
        with Session(self.engine) as session:
            self.assertEqual(repo.get(session, operation.id).state, "succeeded")
