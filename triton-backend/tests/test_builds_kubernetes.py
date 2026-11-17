"""Real workload/UID contract; this fixture does not qualify a container builder."""

import os
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from app.db.entities import BuildAttemptEntity
from app.exceptions import ConflictError
from app.services.builds import jobs_kubernetes as provider
from tests.test_build_qualification import policy

IMAGE = "busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
DIGEST = "sha256:" + "d" * 64


@unittest.skipUnless(os.getenv("BUILD_TEST_KUBERNETES") == "1", "Opt-in Kubernetes build provider tests")
class BuildKubernetesTests(unittest.TestCase):
    def setUp(self):
        self.jobs = provider.Jobs()
        self.namespace = "tc-build-test-" + uuid4().hex[:10]
        self.jobs.core.create_namespace({"metadata": {"name": self.namespace}})
        self.addCleanup(self.jobs.close)
        self.addCleanup(self.jobs.core.delete_namespace, self.namespace)
        self.row = BuildAttemptEntity(operation_id=uuid4().hex, number=1, context_id="a" * 32,
            policy=policy(namespace=self.namespace).model_dump(), executor_image=IMAGE,
            context_receipt={"storage_binding": {"bucket": "fixture"}, "key": "builds/fixture.tar.gz",
                             "dockerfile": "Dockerfile"}, destination="registry.example/allowed:fixture",
            job_name="build-fixture", namespace=self.namespace,
            deadline_at=datetime.utcnow() + timedelta(minutes=2))
        self.jobs.core.create_namespaced_secret(self.namespace, {
            "metadata": {"name": self.row.job_name, "labels": {provider.LABEL: provider.identity(self.row)}},
            "stringData": {"context": "https://fixture.test?signature=sensitive", "config.json": '{"auths":{}}'},
        })

    def create(self, command, deadline=30):
        body = provider.manifest(self.row)
        body["spec"]["activeDeadlineSeconds"] = deadline
        container = body["spec"]["template"]["spec"]["containers"][0]
        container.update(image=IMAGE, command=["/bin/sh", "-ec"], args=[command])
        with patch.object(provider, "manifest", return_value=body):
            resource = self.jobs.create(self.row)
        self.row.job_uid = resource.metadata.uid
        return resource

    def wait(self, predicate, seconds=60):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.5)
        self.fail("Kubernetes fixture deadline exceeded")

    def cleanup_owned(self):
        self.wait(lambda: self.jobs.cleanup(self.row))
        self.assertIsNone(self.jobs.secret(self.row))

    def test_suspended_recovery_executor_digest_logs_and_uid_cleanup(self):
        # Arrange
        resource = self.create(f"echo https://fixture.test?signature=sensitive; echo built; "
                               f"echo {DIGEST} > /dev/termination-log")
        # Act: reconstruct the provider after the write, as after a worker restart.
        recovered = provider.Jobs()
        self.addCleanup(recovered.close)
        observed = recovered.read(self.row)
        # Assert
        self.assertEqual(observed.metadata.uid, resource.metadata.uid)
        self.assertTrue(observed.spec.suspend)
        self.wait(lambda: recovered.resume(self.row, recovered.read(self.row)))
        self.wait(lambda: recovered.observe(self.row, recovered.read(self.row)).terminal)
        result = recovered.observe(self.row, recovered.read(self.row))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.digest, DIGEST)
        self.assertIn("built", result.logs)
        self.assertNotIn("sensitive", result.logs)
        self.assertIsNotNone(recovered.read(self.row))
        self.cleanup_owned()

    def test_deadline_is_failed_without_digest_and_cleanup_is_uid_guarded(self):
        # Arrange
        self.create("echo started; sleep 120", deadline=2)
        self.wait(lambda: self.jobs.resume(self.row, self.jobs.read(self.row)))
        # Act
        self.wait(lambda: self.jobs.observe(self.row, self.jobs.read(self.row)).terminal)
        result = self.jobs.observe(self.row, self.jobs.read(self.row))
        # Assert
        self.assertFalse(result.succeeded)
        self.assertIsNone(result.digest)
        uid = self.row.job_uid
        self.row.job_uid = "foreign-uid"
        with self.assertRaises(ConflictError):
            self.jobs.cleanup(self.row)
        self.row.job_uid = uid
        self.cleanup_owned()

    def test_cancel_running_job_deletes_pods_and_secret(self):
        # Arrange
        self.create("echo started; sleep 120")
        self.wait(lambda: self.jobs.resume(self.row, self.jobs.read(self.row)))
        self.wait(lambda: "started" in self.jobs.observe(self.row, self.jobs.read(self.row)).logs)
        # Act / Assert
        self.cleanup_owned()
        self.assertIsNone(self.jobs.read(self.row))
