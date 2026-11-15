"""Opt-in real Kubernetes + PostgreSQL recovery; uses only a uniquely named test namespace.

The HTTP fixture exercises Kubernetes probes/controller state, not Triton inference or GPUs.
"""

import os
import time
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from sqlmodel import Session

from app.core.operation_secrets import decode, encode
from app.repositories import deployment_intents as intents
from app.repositories import operations as repo
from app.services.operations import ExecutionContext
from app.services.deployment import operations as deploy
from app.services.deployment.operation_provider import DeploymentProvider, OwnershipConflict
from tests import test_deployment_operations as shared
from tests import test_deployment_operations_postgres as pg


@unittest.skipUnless(os.getenv("DEPLOYMENT_TEST_KUBERNETES") == "1" and pg.URL,
                     "Set DEPLOYMENT_TEST_KUBERNETES=1 and OPERATIONS_TEST_DATABASE_URL")
class DeploymentKubernetesTests(unittest.TestCase):
    def seed(self):
        shared.DeploymentOperationTests.seed(self)

    def submit(self):
        return shared.DeploymentOperationTests.submit(self)

    def step(self, operation_id):
        return shared.DeploymentOperationTests.step(self, operation_id)

    def cleanup_schema(self):
        pg.DeploymentPostgresTests.cleanup_schema(self)

    def setUp(self):
        pg.DeploymentPostgresTests.setUp(self)
        self.namespace = "tc-operation-test-" + uuid4().hex[:12]
        self.enterContext(patch.object(deploy, "in_cluster_namespace", return_value=self.namespace))
        self.enterContext(patch.object(deploy, "DeploymentProvider", DeploymentProvider))
        self.provider = DeploymentProvider()
        self.addCleanup(self.provider.close)
        self.addCleanup(self.cleanup_namespace)
        self.provider.namespace(self.namespace, lambda: None)

    def cleanup_namespace(self):
        # Never clean by user input or use a shared namespace.
        assert self.namespace.startswith("tc-operation-test-")
        self.provider.call(f"/api/v1/namespaces/{self.namespace}", "DELETE", {"kind": "DeleteOptions"})

    def wait_state(self, operation_id, expected):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = self.step(operation_id)
            if result.state in expected:
                return result
            self.assertNotEqual(result.state, "failed", result.message)
            time.sleep(1)
        self.fail(f"Operation did not reach {expected}: {result}")

    def test_real_lost_ack_readiness_uid_fencing_and_owned_cleanup(self):
        result = self.submit()
        with Session(self.engine) as session:
            intent = intents.get(session, result.operation_id)
            bundle = decode(intent.bundle_enc)
            deployment = next(m for m in bundle["manifests"] if m["kind"] == "Deployment")
            # Use a small architecture-independent fixture instead of requiring GPU images/S3.
            pod = deployment["spec"]["template"]["spec"]
            pod.clear()
            pod["containers"] = [{"name": "fixture", "image": "python:3.12-alpine",
                "command": ["sh", "-c", "mkdir -p /tmp/web/v2/health; echo ok > /tmp/web/v2/health/ready; cd /tmp/web; exec python -m http.server 8000"],
                "readinessProbe": {"httpGet": {"path": "/v2/health/ready", "port": 8000},
                                   "periodSeconds": 1, "initialDelaySeconds": 1},
                "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                              "limits": {"cpu": "100m", "memory": "64Mi"}}}]
            intent.bundle_enc = encode(bundle)
            session.add(intent)
            session.commit()
        original = DeploymentProvider.call
        lost: list[str] = []
        def lose_ack(provider, path, method, body=None, query=None):
            output = original(provider, path, method, body, query)
            if method == "POST" and path.endswith("/deployments") and not lost:
                lost.append(output["metadata"]["uid"])
                raise TimeoutError("simulated lost response after real Kubernetes create")
            return output
        with patch.object(DeploymentProvider, "call", lose_ack):
            self.assertEqual(self.step(result.operation_id).state, "pending")
        self.assertEqual(len(lost), 1)
        ready = self.wait_state(result.operation_id, {"succeeded"})
        self.assertEqual(ready.result["deployment_uid"], lost[0])
        resources = self.provider.call(f"/apis/apps/v1/namespaces/{self.namespace}/deployments", "GET")
        self.assertEqual(len(resources["items"]), 1)
        # A replacement with another UID is never adopted, even with a copied owner label.
        with self.assertRaises(OwnershipConflict):
            self.provider.ensure(deployment, result.operation_id, "wrong-uid", lambda: None)
        unrelated = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "unrelated"},
                     "data": {"retain": "yes"}}
        self.provider.call(f"/api/v1/namespaces/{self.namespace}/configmaps", "POST", unrelated)
        with Session(self.engine) as session:
            with self.assertRaises(deploy.ConflictError):
                deploy.prepare_deletion(session, result.instance_id)
        self.wait_state(result.operation_id, {"cancelled"})
        self.assertIsNotNone(self.provider.call(f"/api/v1/namespaces/{self.namespace}/configmaps/unrelated", "GET"))
        self.assertEqual(self.provider.pods(self.namespace, result.operation_id), [])

    def test_restart_deadline_retry_and_deletion_fence(self):
        result = self.submit()
        with Session(self.engine) as session:
            intent = intents.get(session, result.operation_id)
            bundle = decode(intent.bundle_enc)
            deployment = next(m for m in bundle["manifests"] if m["kind"] == "Deployment")
            deployment["spec"]["replicas"] = 0  # Real controller, deterministically never serving-ready.
            intent.bundle_enc = encode(bundle)
            session.add(intent)
            session.commit()
            old = repo.claim(session, "crashed-before-write", [intents.KIND], 30)
            row = repo.get(session, old.id)
            row.lease_until = repo.now(session) - timedelta(seconds=1)
            session.add(row)
            session.commit()
        self.assertEqual(self.step(result.operation_id).state, "pending")
        actual = self.provider.read(deployment)
        uid = actual["metadata"]["uid"]
        with self.assertRaises(deploy.ConflictError):
            deploy.adapter.reconcile(old, ExecutionContext(self.service, lambda: Session(self.engine), old))
        with Session(self.engine) as session:
            intent = intents.get(session, result.operation_id)
            intent.deadline_at = repo.now(session) - timedelta(seconds=1)
            session.add(intent)
            session.commit()
        self.assertEqual(self.step(result.operation_id).state, "failed")
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "pending")
        self.assertEqual(self.provider.read(deployment)["metadata"]["uid"], uid)
        with Session(self.engine) as session:
            with self.assertRaises(deploy.ConflictError):
                deploy.prepare_deletion(session, result.instance_id)
        self.wait_state(result.operation_id, {"cancelled"})
        with Session(self.engine) as session:
            from app.repositories import instances
            self.assertTrue(deploy.prepare_deletion(session, result.instance_id))
            instances.delete(session, instances.find_by_id(session, result.instance_id))
        self.step(result.operation_id)
        self.assertIsNone(self.provider.read(deployment))

    def test_foreign_resource_and_partial_creation_cancellation(self):
        result = self.submit()
        path = f"/api/v1/namespaces/{self.namespace}/secrets"
        foreign = self.provider.call(path, "POST", {"apiVersion": "v1", "kind": "Secret",
                                     "metadata": {"name": "iris-s3-credentials"}})
        self.assertEqual(self.step(result.operation_id).state, "failed")
        original_uid = foreign["metadata"]["uid"]
        self.assertEqual(self.provider.call(path + "/iris-s3-credentials", "GET")["metadata"]["uid"], original_uid)
        with Session(self.engine) as session:
            self.service.cancel(session, self.claims, result.operation_id)
        self.assertEqual(self.step(result.operation_id).state, "pending")
        self.assertEqual(self.provider.call(path + "/iris-s3-credentials", "GET")["metadata"]["uid"], original_uid)
        # Test owner removes its foreign fixture; operation can finish its previously blocked cleanup.
        self.provider.call(path + "/iris-s3-credentials", "DELETE", {
            "kind": "DeleteOptions", "preconditions": {"uid": original_uid}})
        self.wait_state(result.operation_id, {"cancelled"})
        rows = self.provider.call(f"/apis/apps/v1/namespaces/{self.namespace}/deployments", "GET")
        self.assertEqual(rows["items"], [])
