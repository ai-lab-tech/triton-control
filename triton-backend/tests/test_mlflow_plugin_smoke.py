"""Regression coverage for the published-plugin deployment contract verifier."""

import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

from scripts.smoke_mlflow_plugin import verify_deployment


class PluginError(Exception):
    pass


class MlflowPluginSmokeTests(unittest.TestCase):
    def test_contract_accepts_global_profiles_and_checks_missing_profiles_and_deployment_ownership(self):
        # Arrange
        client = MagicMock()
        client.create_deployment.side_effect = [
            {"name": "mlflow-contract", "status": "ready"},
            {"name": "global-profile", "status": "ready"},
            PluginError("404"),
        ]
        client.get_deployment.side_effect = [
            {"model_name": "classifier"}, PluginError("404"), {"model_name": "classifier"},
        ]
        client.list_deployments.side_effect = [[{"name": "mlflow-contract"}], [], []]
        client.delete_deployment.return_value = {"status": "deleted"}
        deployments = ModuleType("mlflow.deployments")
        deployments.get_deploy_client = MagicMock(return_value=client)
        exceptions = ModuleType("mlflow.exceptions")
        exceptions.MlflowException = PluginError
        modules = {"mlflow": ModuleType("mlflow"), "mlflow.deployments": deployments, "mlflow.exceptions": exceptions}
        response = MagicMock()
        response.json.return_value = [{
            "model_uri": "s3://compatibility-models/classifier", "model_name": "classifier",
            "requirements_txt": "numpy", "image": "triton:contract", "model_control_mode": "explicit",
            "inference_examples": {"classifier": {"inputs": []}},
        }]
        with tempfile.TemporaryDirectory() as folder:
            repository = Path(folder)
            (repository / "requirements.txt").write_text("numpy")
            (repository / "request.json").write_text(json.dumps({"inputs": []}))
            with patch.dict("sys.modules", modules), patch("httpx.get", return_value=response):
                # Act
                verify_deployment("runs:/run/classifier", repository,
                                  {"url": "http://fixture", "token": "owner", "other_token": "other"})
        # Assert
        calls = client.create_deployment.call_args_list
        self.assertEqual([call.kwargs["config"]["s3_profile_name"] for call in calls],
                         ["models", "other-owner", "does-not-exist"])
        self.assertEqual([call.args[0] for call in client.delete_deployment.call_args_list],
                         ["global-profile", "mlflow-contract"])
        self.assertEqual(client.list_deployments.call_count, 3)
