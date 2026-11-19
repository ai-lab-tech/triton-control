"""Deployment admission and recovery using the real adapter and a faulting Kubernetes API."""

import base64
import copy
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.deployment_api import router
from app.core.operation_secrets import decode
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import DeploymentIntentEntity, OperationEntity, S3ProfileEntity, TritonInstanceEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.repositories import deployment_intents as intents
from app.repositories import instances, operations as repo
from app.schemas import CreateDeploymentRequest
from app.services.deployment import operations as deploy
from app.services.deployment.operation_provider import DeploymentProvider, OWNER, OwnershipConflict
from app.services.operations import OperationService, OperationWorker


class MemoryProvider(DeploymentProvider):
    def __init__(self):
        self.resources = {}
        self.writes = 0
        self.lose_ack = False
        self.reject = False
        self.available = False
        self.deletes = []

    def close(self):
        pass

    def call(self, path, method, body=None, query=None):
        if path.endswith("/pods"):
            return {"items": []}
        if method == "GET":
            if path not in self.resources:
                raise ApiException(status=404)
            result = copy.deepcopy(self.resources[path])
            if result.get("kind") == "Deployment" and self.available:
                result["status"] = {"observedGeneration": 1, "updatedReplicas": 1,
                                    "availableReplicas": 1, "readyReplicas": 1}
            return result
        if method == "POST":
            if self.reject:
                raise ApiException(status=422, reason="sensitive provider body")
            name = path + "/" + body["metadata"]["name"]
            if name in self.resources:
                raise ApiException(status=409)
            result = copy.deepcopy(body)
            result["metadata"].update(uid=uuid4().hex, resourceVersion="1", generation=1)
            if "stringData" in result:
                result["data"] = {k: base64.b64encode(v.encode()).decode()
                                  for k, v in result.pop("stringData").items()}
            self.resources[name] = result
            self.writes += 1
            if self.lose_ack and result.get("kind") == "Deployment":
                self.lose_ack = False
                raise TimeoutError("secret in lost acknowledgement")
            return copy.deepcopy(result)
        if method == "DELETE":
            old = self.resources[path]
            assert body["preconditions"] == {"uid": old["metadata"]["uid"], "resourceVersion": "1"}
            self.deletes.append(path)
            del self.resources[path]
            return {}
        raise AssertionError(method)


class DeploymentOperationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.enterContext(patch("app.repositories.operations.now", lambda _: datetime.utcnow()))
        self.seed()

    def seed(self):
        self.enterContext(patch.dict(os.environ, {"OPERATION_SECRET_ENCRYPTION_KEY": "deployment-test-key",
                                                 "TRITON_CONTROL_MODE": "development", "POD_NAMESPACE": ""}))
        self.enterContext(patch.object(deploy, "in_cluster_namespace", return_value=""))
        with Session(self.engine) as session:
            session.add_all([UserEntity(id=1, email="anna@test.invalid", name="Anna", role="member"),
                             UserEntity(id=2, email="ben@test.invalid", name="Ben", role="member")])
            session.commit()
            session.add(S3ProfileEntity(id=1, owner_user_id=1, name="fixture", endpoint="http://s3.invalid",
                                        bucket="models", access_key="fixture", secret_key_enc="fixture-secret"))
            session.commit()
        self.enterContext(patch.object(deploy, "collect_deployment_examples", return_value={}))
        self.service = OperationService()
        self.service.register(intents.KIND, deploy.adapter)
        self.enterContext(patch.object(deploy, "service", self.service))
        self.provider = MemoryProvider()
        self.enterContext(patch.object(deploy, "DeploymentProvider", return_value=self.provider))
        self.claims = {"email": "anna@test.invalid", "role": "member"}
        self.request = CreateDeploymentRequest(deployment_name="iris", image="fixture-image", s3_profile_id=1)

    def submit(self, key="test-key", request=None, claims=None):
        with Session(self.engine) as session:
            return deploy.submit(request or self.request, session, claims or self.claims, key)

    def step(self, operation_id):
        with Session(self.engine) as session:
            row = repo.get(session, operation_id)
            row.available_at = repo.now(session) - timedelta(seconds=1)
            session.add(row)
            session.commit()
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        with Session(self.engine) as session:
            return self.service.read(session, self.claims, operation_id)

    def test_lineage_survives_persistence_export_and_retries(self):
        import json
        from pathlib import Path
        from app.schemas.control_lineage import ControlLineage
        from app.services.deployment.mlflow import _lineage, _owned
        from app.exceptions import NotFoundError
        fixture = json.loads((Path(__file__).resolve().parents[2] /
                              "contracts/lineage/v1/control-lineage.fixture.json").read_text())["lineage"]
        request = self.request.model_copy(update={
            "model_uri": "s3://models/repository/iris", "model_name": "iris", "repository_prefix": "repository",
            "lineage": ControlLineage.model_validate(fixture),
        })
        response = self.submit(request=request)
        self.assertEqual(response.lineage.model_dump(mode="json"), fixture)
        with Session(self.engine) as session:
            self.assertEqual(_lineage(session, response.instance_id), fixture)
            with self.assertRaises(NotFoundError):
                _owned(session, {"email": "ben@test.invalid", "role": "member"}, "iris")
        self.assertEqual(self.submit(request=request).operation_id, response.operation_id)
        self.assertEqual(deploy.input_data(self.request).get("lineage", "absent"), "absent")

    def test_admission_is_atomic_secret_safe_and_replay_ignores_changed_defaults(self):
        result = self.submit()
        self.assertEqual(self.provider.writes, 0)
        with Session(self.engine) as session:
            row = repo.get(session, result.operation_id)
            intent = intents.get(session, row.id)
            self.assertNotIn("fixture-secret", intent.bundle_enc)
            self.assertNotIn("fixture-secret", str(row.model_dump()))
            self.assertIn("fixture-secret", str(decode(intent.bundle_enc)))
            self.assertEqual(len(session.exec(select(TritonInstanceEntity)).all()), 1)
        with patch.object(deploy, "resolve_profile_request", side_effect=AssertionError("must not resolve again")):
            self.assertEqual(self.submit().operation_id, result.operation_id)
        with self.assertRaises(ConflictError):
            self.submit(request=self.request.model_copy(update={"image": "changed"}))
        with self.assertRaises(ConflictError):
            self.submit(key="another")
        with Session(self.engine) as session:
            recovered = deploy.find_request(session, self.claims, "test-key", "IRIS")
            self.assertEqual(recovered.operation_id, result.operation_id)
        with patch.object(deploy, "upsert_deployed_instance", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.submit(request=self.request.model_copy(update={"deployment_name": "other"}))
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(OperationEntity)).all()), 1)

    def test_lost_ack_and_new_worker_recover_without_duplicate_creation(self):
        result = self.submit()
        self.provider.lose_ack = True
        first = self.step(result.operation_id)
        self.assertEqual(first.state, "pending")
        self.assertNotIn("secret", first.message)
        self.assertEqual(self.step(result.operation_id).state, "pending")
        writes = self.provider.writes
        self.provider.available = True
        self.assertEqual(self.step(result.operation_id).state, "succeeded")
        self.assertEqual(self.provider.writes, writes)
        self.assertEqual(sum(r.get("kind") == "Deployment" for r in self.provider.resources.values()), 1)

    def test_rejection_retry_and_readiness_deadline(self):
        result = self.submit()
        self.provider.reject = True
        self.assertEqual(self.step(result.operation_id).state, "failed")
        self.provider.reject = False
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, result.operation_id)
            intent = intents.get(session, result.operation_id)
            intent.deadline_at = repo.now(session) - timedelta(seconds=1)
            session.add(intent)
            session.commit()
        self.assertEqual(self.step(result.operation_id).state, "failed")
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, result.operation_id)
            self.assertGreater(intents.get(session, result.operation_id).deadline_at, repo.now(session))
        self.provider.available = True
        self.step(result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "succeeded")

    def test_cancel_after_partial_write_cleans_only_owned_objects(self):
        result = self.submit()
        self.provider.lose_ack = True
        self.step(result.operation_id)
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, result.operation_id)
        self.step(result.operation_id)
        self.step(result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "cancelled")
        self.assertEqual(list(self.provider.resources), ["/api/v1/namespaces/iris"])
        with Session(self.engine) as session:
            self.assertTrue(deploy.prepare_deletion(session, result.instance_id))
            instances.delete(session, instances.find_by_id(session, result.instance_id))
            self.assertIsNone(intents.get(session, result.operation_id).reservation)
            self.assertEqual(self.service.read(session, self.claims, result.operation_id).state, "cancelled")
        with self.assertRaises(ConflictError):
            self.submit()  # old key never recreates a deleted instance

    def test_cancel_before_first_execution_and_deletion_of_completed_instance(self):
        result = self.submit()
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "cancelled")
        self.assertEqual(self.provider.writes, 0)
        other = self.submit(request=self.request.model_copy(update={"deployment_name": "other"}))
        self.provider.available = True
        self.step(other.operation_id)
        self.step(other.operation_id)
        with Session(self.engine) as session:
            with self.assertRaises(ConflictError):
                deploy.prepare_deletion(session, other.instance_id)
        self.step(other.operation_id)
        self.step(other.operation_id)
        self.assertEqual(self.step(other.operation_id).state, "cancelled")

    def test_production_cutover_allows_authorized_cancellation_and_cleanup(self):
        # Arrange: admit and partially execute in development.
        result = self.submit()
        self.step(result.operation_id)
        writes = self.provider.writes
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}):
            # Act: request cancellation only after the mode changes.
            with Session(self.engine) as session:
                cancelled = self.service.cancel(session, self.claims, result.operation_id)
            self.step(result.operation_id)
            self.step(result.operation_id)
            outcome = self.step(result.operation_id)
            # Assert: cleanup finishes without creating additional resources.
            self.assertTrue(cancelled.cancel_requested)
            self.assertEqual(outcome.state, "cancelled")
            self.assertEqual(self.provider.writes, writes)
            self.assertTrue(self.provider.deletes)
            with self.assertRaises(ForbiddenError):
                self.submit(request=self.request.model_copy(update={"deployment_name": "new"}))
            with Session(self.engine) as session, self.assertRaises(ForbiddenError):
                self.service.retry(session, self.claims, result.operation_id)

    def test_production_cancellation_still_requires_instance_access(self):
        # Arrange
        result = self.submit()
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}), Session(self.engine) as session:
            # Act / Assert
            with self.assertRaises(ForbiddenError):
                self.service.cancel(session, {"email": "ben@test.invalid"}, result.operation_id)
            self.assertFalse(repo.get(session, result.operation_id).cancel_requested)

    def test_current_permissions_and_production_mode_prevent_writes(self):
        result = self.submit()
        with Session(self.engine) as session:
            with self.assertRaises(ForbiddenError):
                self.service.read(session, {"email": "ben@test.invalid"}, result.operation_id)
            user = session.get(UserEntity, 1)
            user.assigned_instances = []
            session.add(user)
            session.commit()
        with self.assertRaises(ForbiddenError):
            self.submit()
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        self.assertEqual(self.provider.writes, 0)
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}):
            with self.assertRaises(ForbiddenError):
                self.submit()

    def test_existing_foreign_resource_is_never_overwritten_or_deleted(self):
        result = self.submit()
        path = "/api/v1/namespaces/iris/secrets/iris-s3-credentials"
        self.provider.resources[path] = {"kind": "Secret", "metadata": {"name": "iris-s3-credentials",
                                          "namespace": "iris", "uid": "foreign", "resourceVersion": "1"}}
        self.assertEqual(self.step(result.operation_id).state, "failed")
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, result.operation_id)
        self.step(result.operation_id)
        self.assertIn(path, self.provider.resources)
        self.assertNotIn(path, self.provider.deletes)

    def test_provider_fences_uid_changes_and_detects_drift(self):
        manifest = {"kind": "ConfigMap", "metadata": {"namespace": "iris", "name": "owned",
                    "labels": {OWNER: "op"}}, "data": {"input": "frozen"}}
        result = self.provider.ensure(manifest, "op", None, lambda: None)
        with self.assertRaises(OwnershipConflict):
            self.provider.ensure(manifest, "op", "different-uid", lambda: None)
        changed = copy.deepcopy(manifest)
        changed["data"]["input"] = "changed"
        with self.assertRaises(OwnershipConflict):
            self.provider.ensure(changed, "op", result["metadata"]["uid"], lambda: None)

    def test_api_202_location_and_legacy_response_fields(self):
        app = FastAPI()
        app.include_router(router)
        def sessions():
            with Session(self.engine) as session:
                yield session
        app.dependency_overrides[get_session] = sessions
        app.dependency_overrides[get_claims] = lambda: self.claims
        with TestClient(app) as client:
            response = client.post("/api/deployments", json=self.request.model_dump(mode="json"))
            self.assertEqual(response.status_code, 202, response.text)
            body = response.json()
            self.assertEqual(response.headers["Location"], body["status_url"])
            self.assertIn("instance_id", body)
            self.assertEqual(body["applied_resources"], [])
            self.assertEqual(client.post("/api/deployments", json=self.request.model_dump(mode="json")).json(), body)

    def test_replay_rechecks_delegated_token_scope(self):
        self.submit()
        with self.assertRaises(BadRequestError):
            self.submit(claims={**self.claims, "token_use": "mlflow_workflow",
                                "allowed_s3_profile_id": 1, "allowed_deployment_name": "another"})

    def test_quantity_normalization_and_status_do_not_relax_secret_or_input_checks(self):
        from app.services.deployment.operation_provider import subset
        self.assertTrue(subset({"resources": {"requests": {"cpu": "1000m", "memory": "1024Mi"}}},
                               {"resources": {"requests": {"cpu": "1", "memory": "1Gi"}}}))
        self.assertTrue(subset({"readinessProbe": {"initialDelaySeconds": 0}}, {"readinessProbe": {}}))
        self.assertFalse(subset({"readinessProbe": {"initialDelaySeconds": 1}}, {"readinessProbe": {}}))
        self.assertFalse(subset({"stringData": {"value": "1000m"}}, {"stringData": {"value": "1"}}))

    def test_recovery_reads_renew_lease_even_when_nothing_needs_creating(self):
        result = self.submit()
        self.step(result.operation_id)
        writes = self.provider.writes
        clock = [datetime.utcnow()]
        original = self.provider.call
        def slow_read(path, method, *args, **kwargs):
            response = original(path, method, *args, **kwargs)
            if method == "GET":
                clock[0] += timedelta(seconds=10)
            return response
        self.provider.available = True
        with patch.object(repo, "now", side_effect=lambda _: clock[0]), patch.object(self.provider, "call", slow_read):
            self.assertEqual(self.step(result.operation_id).state, "succeeded")
        self.assertEqual(self.provider.writes, writes)
