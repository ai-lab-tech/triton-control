"""Job outcomes survive a pod disappearing between listing and log retrieval."""

import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.db.entities import BuildAttemptEntity
from app.services.builds.jobs_kubernetes import Jobs


class JobObservationTests(unittest.TestCase):
    def fixture(self, conditions, *, terminated=None, status=404):
        jobs = object.__new__(Jobs)
        jobs.core = MagicMock()
        jobs.sensitive_values = MagicMock(return_value=[])
        jobs.core.list_namespaced_pod.return_value.items = [NS(
            metadata=NS(name="executor-pod", owner_references=[NS(uid="job-uid", kind="Job", controller=True)]),
            status=NS(container_statuses=[NS(
                name="executor", state=NS(running=None if terminated else NS(), terminated=terminated),
            )]),
        )]
        jobs.core.read_namespaced_pod_log.side_effect = ApiException(status=status)
        row = BuildAttemptEntity(operation_id="a" * 32, number=1, namespace="build-test", job_name="build")
        resource = NS(metadata=NS(uid="job-uid"), status=NS(conditions=conditions))
        return jobs, row, resource

    def test_deadline_failure_is_observed_when_pod_logs_disappear(self):
        # Arrange
        jobs, row, resource = self.fixture([NS(type="Failed", status="True", reason="DeadlineExceeded")])
        # Act
        result = jobs.observe(row, resource)
        # Assert
        self.assertTrue(result.terminal)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.running)
        self.assertIsNone(result.digest)
        self.assertEqual(result.logs, "")
        jobs.sensitive_values.assert_not_called()

    def test_missing_logs_do_not_invent_a_terminal_job_outcome(self):
        # Arrange
        jobs, row, resource = self.fixture([])
        # Act
        result = jobs.observe(row, resource)
        # Assert
        self.assertFalse(result.terminal)
        self.assertFalse(result.succeeded)
        self.assertFalse(result.running)
        self.assertIsNone(result.digest)

    def test_missing_logs_preserve_observed_owned_executor_digest(self):
        # Arrange
        digest = "sha256:" + "a" * 64
        jobs, row, resource = self.fixture(
            [NS(type="Complete", status="True")], terminated=NS(exit_code=0, message=digest),
        )
        # Act
        result = jobs.observe(row, resource)
        # Assert
        self.assertTrue(result.terminal)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.digest, digest)
        self.assertEqual(result.logs, "")

    def test_other_log_api_errors_are_not_hidden(self):
        for status in (403, 500):
            with self.subTest(status=status):
                # Arrange
                jobs, row, resource = self.fixture([NS(type="Failed", status="True")], status=status)
                # Act
                with self.assertRaises(ApiException) as error:
                    jobs.observe(row, resource)
                # Assert
                self.assertEqual(error.exception.status, status)
