"""Exercise the keyless client contract over HTTP using the compatibility fixture."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx

from scripts.mlflow_contract_server import record_applied_deployment


class DeploymentClientContractTests(unittest.TestCase):
    def test_keyless_202_readiness_replay_and_delete_migration(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryFile(mode="w+") as server_log:
            connection = Path(folder) / "connection.json"
            process = subprocess.Popen([sys.executable, "scripts/mlflow_contract_server.py",
                                        "--connection-file", str(connection)], stdout=server_log,
                                       stderr=server_log)
            try:
                deadline = time.monotonic() + 20
                while not connection.exists():
                    self.assertIsNone(process.poll(), "Contract server exited")
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.1)
                config = json.loads(connection.read_text())
                with httpx.Client(
                    base_url=config["url"], headers={"Authorization": "Bearer " + config["token"]},
                ) as client:
                    payload = {"deployment_name": "contract", "image": "fixture", "s3_profile_id": 7,
                               "model_uri": "s3://compatibility-models/models/classifier",
                               "model_name": "classifier", "repository_prefix": "models"}
                    accepted = client.post("/api/deployments", json=payload)
                    accepted.raise_for_status()  # Published client accepts all successful status codes, including 202.
                    self.assertEqual(accepted.status_code, 202)
                    replay = client.post("/api/deployments", json=payload)
                    self.assertEqual(accepted.json()["instance_id"], replay.json()["instance_id"])
                    self.assertEqual(accepted.json()["operation_id"], replay.json()["operation_id"])
                    while True:
                        current = client.get("/api/deployments/mlflow/contract")
                        current.raise_for_status()
                        if current.json()["status"] == "ready":
                            break
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.1)
                    deleted = client.delete("/api/deployments/mlflow/contract")
                    self.assertEqual(deleted.status_code, 409)
                    self.assertIn("cleanup requested", deleted.text)
                    while deleted.status_code == 409:
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.1)
                        deleted = client.delete("/api/deployments/mlflow/contract")
                    deleted.raise_for_status()
                    self.assertEqual(deleted.json()["status"], "deleted")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if sys.exc_info()[0] is not None:
                    server_log.seek(0)
                    print("Contract server diagnostics:\n" + server_log.read(), file=sys.stderr)


class AppliedDeploymentPublicationTests(unittest.TestCase):
    def test_readers_observe_only_complete_records_during_database_lookup(self):
        # Arrange: a readiness reader runs while the worker is loading the instance.
        applied: list[dict[str, Any]] = []
        observed = []
        request = SimpleNamespace(model_uri="s3://models/iris", model_name="iris",
                                  requirements_txt=None, image="fixture", model_control_mode="none")
        instance = SimpleNamespace(url="http://fixture:18000", inference_examples={"iris": {"inputs": []}})
        session = MagicMock()

        def load_instance(*_args):
            observed.append([dict(record) for record in applied])
            return instance

        session.get.side_effect = load_instance
        # Act
        record_applied_deployment(applied, 1, request, session)
        # Assert
        self.assertEqual(observed, [[]])
        self.assertEqual(applied, [{**vars(request), "url": instance.url,
                                    "inference_examples": instance.inference_examples}])

    def test_failed_lookup_does_not_publish_a_partial_record(self):
        # Arrange
        applied: list[dict[str, Any]] = []
        request = SimpleNamespace(model_uri="s3://models/iris", model_name="iris",
                                  requirements_txt=None, image="fixture", model_control_mode="none")
        session = MagicMock()
        session.get.side_effect = RuntimeError("Database unavailable")
        # Act / Assert
        with self.assertRaisesRegex(RuntimeError, "Database unavailable"):
            record_applied_deployment(applied, 1, request, session)
        self.assertEqual(applied, [])
