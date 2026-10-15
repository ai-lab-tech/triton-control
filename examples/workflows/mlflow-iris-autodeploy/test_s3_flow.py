"""Integration check: real MLflow proxy uploads, emulated S3, no Argo model copy.

Run with the example dependencies and moto[server] installed:
    python -m unittest discover -s examples/workflows/mlflow-iris-autodeploy -p 'test_*.py'
"""

import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import urlopen

import boto3
import httpx
import mlflow
from mlflow_triton_control import TritonControlDeploymentClient
from moto.server import ThreadedMotoServer


class S3FlowTests(unittest.TestCase):
    def test_training_uploads_both_models_and_outputs_deployable_s3_uri(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
            server.start()
            self.addCleanup(server.stop)
            host, s3_port = server.get_host_and_port()
            endpoint = f"http://{host}:{s3_port}"
            environment = {
                "AWS_ACCESS_KEY_ID": "fixture", "AWS_SECRET_ACCESS_KEY": "fixture",
                "AWS_DEFAULT_REGION": "us-east-1", "MLFLOW_S3_ENDPOINT_URL": endpoint,
                "MLFLOW_TRACKING_TOKEN": "fixture-tracking-token",
                "TRITON_MODEL_URI_OUTPUT": str(root / "uri"),
                "TRITON_MODEL_VERSION_OUTPUT": str(root / "version"),
                "TRITON_REPOSITORY_DIR": str(root / "repository"),
            }
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            tracking_uri = f"http://127.0.0.1:{port}"
            environment["MLFLOW_TRACKING_URI"] = tracking_uri
            with patch.dict(os.environ, environment):
                s3 = boto3.client("s3", endpoint_url=endpoint)
                s3.create_bucket(Bucket="model-artifacts")
                with (root / "server.log").open("w+") as log:
                    process = subprocess.Popen([
                        sys.executable, "-m", "mlflow", "server", "--host", "127.0.0.1",
                        "--port", str(port), "--workers", "1",
                        "--backend-store-uri", f"sqlite:///{root / 'tracking.db'}",
                        "--serve-artifacts", "--artifacts-destination", "s3://model-artifacts/team/mlflow",
                    ], stdout=log, stderr=log, env=os.environ.copy())
                    self.addCleanup(lambda: process.poll() is None and process.kill())
                    for _ in range(120):
                        try:
                            with urlopen(tracking_uri + "/health", timeout=1):
                                break
                        except OSError:
                            if process.poll() is not None:
                                log.seek(0)
                                self.fail(log.read())
                            time.sleep(0.25)
                    else:
                        self.fail("MLflow did not become ready")
                    spec = importlib.util.spec_from_file_location("iris_train", Path(__file__).with_name("train.py"))
                    train = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(train)
                    previous_uri = mlflow.get_tracking_uri()
                    try:
                        # Only custom discovery is substituted; all model uploads use the real HTTP proxy.
                        with patch.object(train, "urlopen", return_value=io.BytesIO(json.dumps({
                            "artifact_root": "s3://model-artifacts/team/mlflow"
                        }).encode())) as discovery:
                            train.main()
                        self.assertEqual(discovery.call_args.args[0].headers["Authorization"],
                                         "Bearer fixture-tracking-token")
                        uri = (root / "uri").read_text()
                        parsed = urlsplit(uri)
                        self.assertEqual(parsed.netloc, "model-artifacts")
                        self.assertTrue(parsed.path.endswith("/model/iris_classifier"))
                        # The unchanged deployment plugin accepts exactly this URI.
                        real_client = httpx.Client
                        requests = []

                        def deploy(request):
                            if request.method == "POST":
                                requests.append(json.loads(request.content))
                                return httpx.Response(200, json={"instance_id": 42})
                            return httpx.Response(200, json={"name": "iris", "status": "ready", "instance_id": 42})

                        with patch.dict(os.environ, {"TRITON_CONTROL_TOKEN": "fixture"}), patch(
                            "mlflow_triton_control.deployment_client.httpx.Client",
                            side_effect=lambda **kwargs: real_client(
                                **kwargs, transport=httpx.MockTransport(deploy)
                            ),
                        ):
                            TritonControlDeploymentClient("triton-control://control.test").create_deployment(
                                "iris", uri, config={"s3_profile_id": 3, "image": "triton:fixture"},
                            )
                        self.assertEqual(requests[0]["model_uri"], uri)
                        self.assertEqual(requests[0]["model_name"], "iris_classifier")
                        prefix = parsed.path.lstrip("/")
                        objects = s3.list_objects_v2(Bucket="model-artifacts")["Contents"]
                        keys = [item["Key"] for item in objects]
                        self.assertIn(prefix + "/config.pbtxt", keys)
                        self.assertIn(prefix + "/1/model.onnx", keys)
                        self.assertTrue(any("sklearn" in key or key.endswith("model.pkl")
                                            or key.endswith("model.skops") for key in keys))
                        config = s3.get_object(Bucket="model-artifacts", Key=prefix + "/config.pbtxt")["Body"].read()
                        self.assertIn(b'name: "iris_classifier"', config)
                        self.assertEqual((root / "version").read_text(), "1")
                    finally:
                        mlflow.set_tracking_uri(previous_uri)
                        process.terminate()
                        process.wait(timeout=15)
