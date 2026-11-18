"""Real Kubernetes cutover inventory in a disposable isolated namespace."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from kubernetes import client  # type: ignore[import-untyped]

from app.services import installation_policy as cutover
from app.services.kubernetes_client import api_client


@unittest.skipUnless(os.getenv("INSTALLATION_TEST_KUBERNETES") == "1", "Opt-in Kubernetes cutover tests")
class InstallationKubernetesTests(unittest.TestCase):
    def setUp(self):
        self.namespace = "tc-policy-test-" + uuid4().hex[:10]
        self.api = api_client()
        self.core = client.CoreV1Api(self.api)
        self.core.create_namespace({"metadata": {"name": self.namespace}})
        self.addCleanup(self.api.close)
        self.addCleanup(self.core.delete_namespace, self.namespace)
        env = patch.dict(os.environ, {key: self.namespace for key in (
            "POD_NAMESPACE", "ARGO_WORKFLOWS_NAMESPACE", "KUBERNETES_NAMESPACE", "TRITON_CONTROL_NAMESPACE",
        )})
        env.start()
        self.addCleanup(env.stop)

    def service(self, name, labels):
        return self.core.create_namespaced_service(self.namespace, {
            "metadata": {"name": name, "labels": labels},
            "spec": {"ports": [{"port": 8000}], "selector": {"app": name}},
        })

    def test_serving_service_survives_clean_cutover(self):
        # Arrange
        serving = self.service("serving", {"app": "triton"})
        # Act
        cutover.verify_cluster_cutover()
        # Assert
        self.assertEqual(self.core.read_namespaced_service("serving", self.namespace).metadata.uid,
                         serving.metadata.uid)

    def test_orphan_development_access_blocks_cutover_without_deletion(self):
        # Arrange
        development = self.service("code-orphan", {"app": "code-server"})
        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "development workloads"):
            cutover.verify_cluster_cutover()
        self.assertEqual(self.core.read_namespaced_service("code-orphan", self.namespace).metadata.uid,
                         development.metadata.uid)
