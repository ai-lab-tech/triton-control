"""Contract checks for the package without requiring an MLflow server."""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

mlflow = types.ModuleType("mlflow")
deployments = types.ModuleType("mlflow.deployments")
exceptions = types.ModuleType("mlflow.exceptions")


class BaseDeploymentClient:
    def __init__(self, target_uri):
        self.target_uri = target_uri


class MlflowException(Exception):
    pass


deployments.BaseDeploymentClient = BaseDeploymentClient
exceptions.MlflowException = MlflowException
with patch.dict(sys.modules, {"mlflow": mlflow, "mlflow.deployments": deployments, "mlflow.exceptions": exceptions}):
    from mlflow_triton_control.deployment_client import TritonControlDeploymentClient


class DeploymentClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TritonControlDeploymentClient("triton-control://control.test")

    def test_create_derives_repository_from_model_uri_and_waits_for_model(self) -> None:
        with (
            patch.object(self.client, "_request") as request,
            patch.dict(os.environ, {"TRITON_CONTROL_TOKEN": "token"}),
        ):
            request.side_effect = [
                {"instance_id": 42},
                {"name": "iris", "status": "starting"},
                {"name": "iris", "status": "ready"},
            ]
            with patch("time.sleep"):
                result = self.client.create_deployment(
                    "iris", "s3://models/dev/iris_classifier",
                    config={"s3_profile_id": 7, "image": "triton:latest"},
                )
        self.assertEqual(result["status"], "ready")
        payload = request.call_args_list[0].kwargs["json"]
        self.assertEqual(payload["repository_prefix"], "dev")
        self.assertEqual(payload["model_name"], "iris_classifier")

    def test_mlflow_registry_uri_is_rejected(self) -> None:
        with self.assertRaises(MlflowException):
            self.client.create_deployment(
                "iris", "models:/iris/1", config={"s3_profile_id": 7, "image": "triton:latest"}
            )

    def test_invalid_timeout_is_rejected_before_create(self) -> None:
        self.client._request = Mock()
        with self.assertRaisesRegex(MlflowException, "wait_timeout_seconds must be a positive number"):
            self.client.create_deployment(
                "iris",
                "s3://models/repository/iris",
                config={"s3_profile_id": 7, "image": "triton:latest", "wait_timeout_seconds": "later"},
            )
        self.client._request.assert_not_called()
