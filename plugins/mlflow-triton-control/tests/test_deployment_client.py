"""Deployment client checks using real MLflow without a remote server."""

import os
import unittest
from unittest.mock import Mock, patch

from mlflow.exceptions import MlflowException
from mlflow_triton_control.deployment_client import TritonControlDeploymentClient


class DeploymentClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TritonControlDeploymentClient("triton-control://control.test")

    def test_create_derives_repository_from_model_uri_and_waits_for_model(self) -> None:
        # Arrange
        with (
            patch.object(self.client, "_request") as request,
            patch.dict(os.environ, {"TRITON_CONTROL_TOKEN": "token"}),
        ):
            request.side_effect = [
                {"instance_id": 42},
                {"name": "iris", "status": "starting"},
                {"name": "iris", "status": "ready"},
            ]
            # Act
            with patch("time.sleep"):
                result = self.client.create_deployment(
                    "iris",
                    "s3://models/dev/iris_classifier",
                    config={"s3_profile_id": 7, "image": "triton:latest"},
                )
        # Assert
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["flavor"], "triton")
        payload = request.call_args_list[0].kwargs["json"]
        self.assertEqual(payload["repository_prefix"], "dev")
        self.assertEqual(payload["model_name"], "iris_classifier")

    def test_mlflow_registry_uri_is_rejected(self) -> None:
        # Arrange
        model_uri = "models:/iris/1"
        config = {"s3_profile_id": 7, "image": "triton:latest"}

        # Act / Assert
        with self.assertRaises(MlflowException):
            self.client.create_deployment("iris", model_uri, config=config)

    def test_invalid_timeout_is_rejected_before_create(self) -> None:
        for timeout in ("later", "nan", "inf", "-inf", 0, -1):
            with self.subTest(timeout=timeout):
                # Arrange
                self.client._request = Mock()
                config = {
                    "s3_profile_id": 7,
                    "image": "triton:latest",
                    "wait_timeout_seconds": timeout,
                }

                # Act / Assert
                with self.assertRaisesRegex(
                    MlflowException, "wait_timeout_seconds must be a positive number"
                ):
                    self.client.create_deployment(
                        "iris", "s3://models/repository/iris", config=config
                    )
                self.client._request.assert_not_called()

    def test_target_rejects_ignored_credentials_query_and_fragment(self):
        # Arrange
        targets = (
            "triton-control://user:password@control.test",
            "triton-control://control.test?option=value",
            "triton-control://control.test#fragment",
            "triton-control:///missing-host",
        )

        # Act / Assert
        for target in targets:
            with self.subTest(target=target), self.assertRaises(MlflowException):
                TritonControlDeploymentClient(target)
