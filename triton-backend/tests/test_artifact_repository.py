"""Linked artifact repository TLS and ownership regression tests."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kubernetes.client import V1ConfigMap, V1ObjectMeta  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError
from app.services.workflows.artifact_repository import delete_repository, repository_data, sync_repository


class ArtifactRepositoryTests(unittest.TestCase):
    def test_tls_and_ca_follow_profile(self):
        profile = SimpleNamespace(endpoint="https://minio:9000/", bucket="models", region="auto",
                                  ca_certificate="public-ca")
        config = json.loads(repository_data(profile, "workflow-secret"))["s3"]
        self.assertEqual(config["endpoint"], "minio:9000")
        self.assertEqual(config["region"], "auto")
        self.assertFalse(config["insecure"])
        self.assertEqual(config["caSecret"], {"name": "workflow-secret", "key": "ca.pem"})
        profile.ca_certificate = ""
        self.assertNotIn("caSecret", json.loads(repository_data(profile, "workflow-secret"))["s3"])
        profile.endpoint = "http://minio:9000"
        self.assertTrue(json.loads(repository_data(profile, "workflow-secret"))["s3"]["insecure"])

    def test_rejects_endpoint_paths_and_embedded_credentials(self):
        for endpoint in ("https://minio/bucket", "https://user:password@minio", "ftp://minio"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                repository_data(SimpleNamespace(endpoint=endpoint), "secret")

    def test_unmanaged_configmap_is_never_changed_or_deleted(self):
        core = MagicMock()
        core.read_namespaced_config_map.return_value = V1ConfigMap(metadata=V1ObjectMeta(labels={}))
        row = SimpleNamespace(secret_name="secret", namespace="ns")
        profile = SimpleNamespace(endpoint="https://minio", bucket="models", region="", ca_certificate="")
        with (patch("app.services.workflows.artifact_repository.api_client"),
              patch("kubernetes.client.CoreV1Api", return_value=core)):
            with self.assertRaises(BadGatewayError):
                sync_repository(row, profile)
            with self.assertRaises(BadGatewayError):
                delete_repository(row)
        core.replace_namespaced_config_map.assert_not_called()
        core.delete_namespaced_config_map.assert_not_called()
