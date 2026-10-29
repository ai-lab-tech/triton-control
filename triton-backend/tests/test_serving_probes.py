"""Probe configuration must survive Helm-to-backend-to-serving rendering."""

import json
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.schemas import CreateDeploymentRequest
from app.services.deployment.kubernetes import _manifests
from app.services.deployment.probes import serving_probes


class ServingProbeTests(unittest.TestCase):
    def test_default_serving_probes_allow_dependency_installation(self):
        # Arrange
        environment = {k: v for k, v in os.environ.items() if k != "TRITON_DEPLOY_PROBES_JSON"}
        with patch.dict(os.environ, environment, clear=True):
            # Act
            probes = serving_probes()
        # Assert
        self.assertEqual(probes["startupProbe"]["httpGet"], {"path": "/v2/health/ready", "port": 8000})
        self.assertEqual(probes["startupProbe"]["initialDelaySeconds"], 0)
        self.assertEqual(probes["startupProbe"]["periodSeconds"] * probes["startupProbe"]["failureThreshold"], 900)
        self.assertEqual(probes["livenessProbe"]["initialDelaySeconds"], 0)
        self.assertEqual(probes["readinessProbe"]["initialDelaySeconds"], 0)
        self.assertEqual(probes["livenessProbe"]["httpGet"], {"path": "/v2/health/live", "port": 8000})

    def test_configured_probes_reach_generated_serving_manifest(self):
        # Arrange
        settings = {
            "startup": {
                "path": "/custom/startup",
                "initialDelaySeconds": 5,
                "periodSeconds": 20,
                "timeoutSeconds": 4,
                "failureThreshold": 120,
            },
            "liveness": {
                "path": "/custom/live",
                "initialDelaySeconds": 1200,
                "periodSeconds": 20,
                "timeoutSeconds": 3,
                "failureThreshold": 8,
            },
            "readiness": {
                "path": "/custom/ready",
                "initialDelaySeconds": 0,
                "periodSeconds": 5,
                "timeoutSeconds": 2,
                "failureThreshold": 24,
            },
        }
        request = CreateDeploymentRequest(
            deployment_name="probe-test",
            image="test:1",
            s3_url="s3://http://minio:9000/models",
            s3_access_key="test",
            s3_secret_key="test",
        )
        with patch.dict(os.environ, {"TRITON_DEPLOY_PROBES_JSON": json.dumps(settings)}):
            # Act
            manifests = _manifests(request, "test", "probe-test", "probe-svc", "probe-secret", "test:1")
        # Assert
        deployment = next(m for m in manifests if m["kind"] == "Deployment")
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        for kind, expected in settings.items():
            actual = container[f"{kind}Probe"]
            self.assertEqual(actual["httpGet"], {"path": expected["path"], "port": 8000})
            self.assertEqual(
                {k: v for k, v in actual.items() if k != "httpGet"}, {k: v for k, v in expected.items() if k != "path"}
            )

    def test_invalid_probe_values_are_rejected(self):
        # Arrange
        defaults = {
            "path": "/live",
            "initialDelaySeconds": 900,
            "periodSeconds": 10,
            "timeoutSeconds": 1,
            "failureThreshold": 6,
        }
        invalid = [
            ("initialDelaySeconds", -1),
            ("periodSeconds", 0),
            ("timeoutSeconds", 0),
            ("failureThreshold", 0),
            ("initialDelaySeconds", True),
            ("periodSeconds", 1.5),
            ("path", "https://example.test"),
            ("path", "/has space"),
            ("unknown", 1),
        ]
        for key, value in invalid:
            with self.subTest(key=key, value=value):
                configured = {"liveness": {**defaults, key: value}}
                with patch.dict(os.environ, {"TRITON_DEPLOY_PROBES_JSON": json.dumps(configured)}):
                    # Act
                    with self.assertRaises(ValidationError) as error:
                        serving_probes()
                # Assert
                self.assertIn(key, str(error.exception))

    def test_invalid_json_is_rejected(self):
        # Arrange
        with patch.dict(os.environ, {"TRITON_DEPLOY_PROBES_JSON": "not json"}):
            # Act
            with self.assertRaises(ValidationError) as error:
                serving_probes()
        # Assert
        self.assertIn("json", str(error.exception).lower())

    def test_existing_configuration_without_startup_uses_startup_defaults(self):
        # Arrange
        existing = {"liveness": {"path": "/v2/health/live", "initialDelaySeconds": 900, "failureThreshold": 6}}
        with patch.dict(os.environ, {"TRITON_DEPLOY_PROBES_JSON": json.dumps(existing)}):
            # Act
            probes = serving_probes()
        # Assert
        self.assertEqual(probes["startupProbe"]["failureThreshold"], 90)
        self.assertEqual(probes["livenessProbe"]["initialDelaySeconds"], 900)

    def test_invalid_startup_budget_is_rejected(self):
        # Arrange
        settings = {"startup": {"path": "/v2/health/ready", "initialDelaySeconds": 0, "failureThreshold": 0}}
        with patch.dict(os.environ, {"TRITON_DEPLOY_PROBES_JSON": json.dumps(settings)}):
            # Act
            with self.assertRaises(ValidationError) as error:
                serving_probes()
        # Assert
        self.assertIn("failureThreshold", str(error.exception))
