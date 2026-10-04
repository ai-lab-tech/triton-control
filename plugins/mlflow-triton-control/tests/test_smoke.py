"""Smoke tests using installed MLflow and a local tracking/registry database."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import mlflow
from mlflow.deployments import get_deploy_client
from mlflow_triton_control import TritonControlDeploymentClient, triton


class SmokeTests(unittest.TestCase):
    def test_installed_entry_point_and_deployment_lifecycle(self):
        # Arrange
        requests = []

        def handle(request):
            requests.append(request)
            if request.method == "POST":
                return httpx.Response(200, json={"instance_id": 42})
            if request.method == "DELETE":
                return httpx.Response(200, json={"message": "deleted"})
            deployment = {"name": "iris", "status": "ready", "instance_id": 42}
            body = [deployment] if request.url.path.endswith("/mlflow") else deployment
            return httpx.Response(200, json=body)

        real_client = httpx.Client
        with (
            patch.dict("os.environ", {"TRITON_CONTROL_TOKEN": "smoke-token"}),
            patch(
                "mlflow_triton_control.deployment_client.httpx.Client",
                side_effect=lambda **kwargs: real_client(
                    **kwargs, transport=httpx.MockTransport(handle)
                ),
            ),
        ):
            # Act
            client = get_deploy_client("triton-control://control.test")
            created = client.create_deployment(
                "iris",
                "s3://models/repository/iris",
                config={"s3_profile_id": 7, "image": "triton:latest"},
            )
            deployments = client.list_deployments()
            deleted = client.delete_deployment("iris")

        # Assert
        self.assertIsInstance(client, TritonControlDeploymentClient)
        self.assertEqual(created["flavor"], "triton")
        self.assertEqual(deployments, [created])
        self.assertEqual(deleted, {"message": "deleted"})
        for request in requests:
            self.assertEqual(request.headers["Authorization"], "Bearer smoke-token")
        self.assertEqual(
            [(request.method, request.url.path) for request in requests],
            [
                ("POST", "/api/deployments"),
                ("GET", "/api/deployments/mlflow/iris"),
                ("GET", "/api/deployments/mlflow"),
                ("DELETE", "/api/deployments/mlflow/iris"),
            ],
        )

    def test_log_register_and_load_real_mlflow_model(self):
        # Arrange
        tracking_uri, registry_uri = (
            mlflow.get_tracking_uri(),
            mlflow.get_registry_uri(),
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                uri = f"sqlite:///{root / 'mlflow.db'}"
                mlflow.set_tracking_uri(uri)
                mlflow.set_registry_uri(uri)
                experiment = mlflow.create_experiment(
                    "triton-smoke", artifact_location=(root / "artifacts").as_uri()
                )
                source = root / "iris"
                (source / "1").mkdir(parents=True)
                (source / "1/model.onnx").write_bytes(b"smoke-model")
                # Act
                with mlflow.start_run(experiment_id=experiment):
                    info = triton.log_model(
                        source, registered_model_name="iris-serving"
                    )
                repositories = [
                    triton.load_model(model_uri)
                    for model_uri in (info.model_uri, "models:/iris-serving/1")
                ]

                # Assert
                self.assertEqual(str(info.registered_model_version), "1")
                for repository in repositories:
                    self.assertEqual(
                        (repository / "1/model.onnx").read_bytes(), b"smoke-model"
                    )
        finally:
            mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_registry_uri(registry_uri)
