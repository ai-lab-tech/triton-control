"""Exercise the keyless client contract over HTTP using the compatibility fixture."""

import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx


class DeploymentClientContractTests(unittest.TestCase):
    def test_keyless_202_readiness_replay_and_delete_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            connection = Path(folder) / "connection.json"
            process = subprocess.Popen([sys.executable, "scripts/mlflow_contract_server.py",
                                        "--connection-file", str(connection)], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 20
                while not connection.exists():
                    self.assertIsNone(process.poll(), "Contract server exited")
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.1)
                config = json.loads(connection.read_text())
                with httpx.Client(base_url=config["url"], headers={"Authorization": "Bearer " + config["token"]}) as client:
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
