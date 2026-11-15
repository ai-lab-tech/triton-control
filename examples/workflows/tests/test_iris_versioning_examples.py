"""Behavioral tests for the standalone examples; external APIs are mocked."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


class IrisVersioningExamplesTests(unittest.TestCase):
    def load_example(self, mode):
        mlflow = ModuleType("mlflow")
        mlflow.set_experiment = MagicMock()
        mlflow.start_run = MagicMock()
        mlflow.log_metric = MagicMock()
        deployments = ModuleType("mlflow.deployments")
        deployments.get_deploy_client = MagicMock()
        plugin = ModuleType("mlflow_triton")
        plugin.sklearn = SimpleNamespace(
            log_model=MagicMock(return_value=SimpleNamespace(model_uri="models:/logged-id"))
        )
        plugin.register_model = MagicMock(return_value=SimpleNamespace(name="isolated-model", version="7"))
        modules = {"mlflow": mlflow, "mlflow.deployments": deployments, "mlflow_triton": plugin}
        path = ROOT / f"mlflow-iris-auto-versioning-on-{mode}/train.py"
        spec = importlib.util.spec_from_file_location(f"iris_{mode}", path)
        module = importlib.util.module_from_spec(spec)
        for name, replacement in modules.items():
            original = sys.modules.get(name)
            sys.modules[name] = replacement

            def restore(key=name, value=original):
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value

            self.addCleanup(restore)
        spec.loader.exec_module(module)
        return module, mlflow, plugin, deployments.get_deploy_client.return_value

    def test_logs_once_and_deploys_the_correct_uri_for_each_registration_path(self):
        for mode in ("log", "register"):
            with self.subTest(mode=mode):
                # Arrange
                module, mlflow, plugin, client = self.load_example(mode)
                settings = {
                    "MLFLOW_REGISTERED_MODEL_NAME": "isolated-model",
                    "TRITON_CONTROL_S3_PROFILE_NAME": "selected-profile",
                    "TRITON_DEPLOYMENT_NAME": "isolated-deployment",
                }
                # Act
                with patch.dict(os.environ, settings), patch("builtins.print"):
                    module.main()
                # Assert
                plugin.sklearn.log_model.assert_called_once()
                args, kwargs = plugin.sklearn.log_model.call_args
                self.assertEqual(kwargs["name"], "isolated-model")
                self.assertEqual(
                    list(kwargs["input_example"].columns),
                    ["sepal_length", "sepal_width", "petal_length", "petal_width"],
                )
                self.assertEqual(args[0].predict(kwargs["input_example"]).tolist(), [0, 2])
                self.assertGreater(mlflow.log_metric.call_args.args[1], 0.9)
                if mode == "log":
                    self.assertEqual(kwargs["registered_model_name"], "isolated-model")
                    plugin.register_model.assert_not_called()
                    expected_uri = "models:/logged-id"
                else:
                    self.assertNotIn("registered_model_name", kwargs)
                    plugin.register_model.assert_called_once_with("models:/logged-id", "isolated-model")
                    expected_uri = "models:/isolated-model/7"
                self.assertEqual(client.create_deployment.call_args.kwargs["model_uri"], expected_uri)
                self.assertEqual(
                    client.create_deployment.call_args.kwargs["config"]["s3_profile_name"], "selected-profile"
                )

    def test_logging_failure_never_creates_a_deployment(self):
        for mode in ("log", "register"):
            with self.subTest(mode=mode):
                # Arrange
                module, _, plugin, client = self.load_example(mode)
                plugin.sklearn.log_model.side_effect = RuntimeError("artifact upload failed")
                # Act
                with self.assertRaisesRegex(RuntimeError, "artifact upload failed"):
                    module.main()
                # Assert
                client.create_deployment.assert_not_called()
                plugin.register_model.assert_not_called()

    def test_registration_failure_never_deploys_the_unregistered_model(self):
        # Arrange
        module, _, plugin, client = self.load_example("register")
        plugin.register_model.side_effect = RuntimeError("registration failed")
        # Act
        with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "registration failed"):
            module.main()
        # Assert
        client.create_deployment.assert_not_called()

    def test_deployment_failure_is_propagated(self):
        for mode in ("log", "register"):
            with self.subTest(mode=mode):
                # Arrange
                module, _, _, client = self.load_example(mode)
                client.create_deployment.side_effect = RuntimeError("deployment failed")
                # Act
                with patch("builtins.print"), self.assertRaisesRegex(RuntimeError, "deployment failed"):
                    module.main()
                # Assert
                client.create_deployment.assert_called_once()
