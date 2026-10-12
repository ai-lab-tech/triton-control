"""Exercise creator enforcement and binary artifacts against real MLflow/nginx.

Run from triton-backend in its development environment. Requires Docker.
Creates isolated test containers and removes them on completion.
"""

from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import mlflow_api
from app.core.security import get_claims
from app.services.mlflow import proxy, tracking


def docker(*args: str) -> str:
    try:
        result = subprocess.run(["docker", *args], check=True, capture_output=True, text=True)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Docker {args[0]} failed:\n{exc.stderr}") from exc
    return result.stdout.strip()


def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    server = f"mlflow-attribution-server-{suffix}"
    gateway = f"mlflow-attribution-gateway-{suffix}"
    fixture_credential = "isolated-smoke-upstream-credential"
    with tempfile.TemporaryDirectory(prefix="mlflow-attribution-") as directory:
        nginx = Path(directory) / "nginx.conf"
        nginx.write_text(tracking.gateway_manifest("test", "mlflow", fixture_credential)["stringData"]["nginx.conf"])
        nginx.chmod(0o644)
        image = "triton-control-mlflow-attribution-test:3.14.0"
        if subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True, check=False,
        ).returncode:
            (Path(directory) / "Dockerfile").write_text(
                "FROM python:3.12-slim\n"
                "RUN python -m pip install --disable-pip-version-check --no-cache-dir "
                "--target /tmp/packages mlflow==3.14.0\n"
                "ENV PYTHONPATH=/tmp/packages PATH=/tmp/packages/bin:$PATH\n"
                "USER 10001:10001\n"
            )
            docker("build", "-q", "-t", image, directory)
        try:
            docker("run", "-d", "--name", server, "--user", "10001:10001", "--cap-drop", "ALL",
                   "-p", "127.0.0.1::5000", image, "sh", "-ec",
                   "exec mlflow server --host 127.0.0.1 --port 5001 --workers 1 "
                   "--allowed-hosts '*' --backend-store-uri sqlite:////tmp/mlflow.db "
                   "--serve-artifacts --artifacts-destination /tmp/artifacts")
            docker("run", "-d", "--name", gateway, "--user", "10001:10001", "--cap-drop", "ALL",
                   "--network", f"container:{server}", "-v", f"{nginx}:/etc/nginx/nginx.conf:ro",
                   "nginx:1.28-alpine", "nginx", "-g", "daemon off;")
            port = docker("port", server, "5000/tcp").split(":")[-1]
            upstream = f"http://127.0.0.1:{port}"
            upstream_headers = {"x-triton-mlflow-gateway": fixture_credential}
            deadline = time.monotonic() + 300
            with httpx.Client(timeout=5) as client:
                while time.monotonic() < deadline:
                    if docker("inspect", "-f", "{{.State.Running}}", gateway) != "true":
                        raise RuntimeError("Tracking gateway exited before readiness")
                    try:
                        if client.get(upstream + "/health", headers=upstream_headers).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("MLflow did not become ready")
                assert client.post(upstream + "/api/2.0/mlflow/runs/create", json={}).status_code == 401

            app = FastAPI()
            app.include_router(mlflow_api.router)
            app.dependency_overrides[get_claims] = lambda: {"email": "alice@example.com", "role": "member"}
            with patch.object(proxy, "_proxy_server_url", return_value=upstream), patch.object(
                proxy, "is_running_in_cluster", return_value=True
            ), patch.object(tracking, "gateway_header", return_value=upstream_headers), patch.object(
                tracking, "authenticate_workload", return_value={"email": "alice@example.com", "role": "member"}
            ), TestClient(app) as client:
                base = "/api/mlflow/tracking/workflows/test/managed-secret"
                api = base + "/api/2.0/mlflow"
                client.headers["Authorization"] = "Bearer fixture-workload-token"
                experiment = client.post(api + "/experiments/create", json={"name": "attribution-smoke"})
                experiment.raise_for_status()
                created = client.post(api + "/runs/create", json={
                    "experiment_id": experiment.json()["experiment_id"], "user_id": "fake-user",
                    "start_time": 0, "tags": [{"key": "mlflow.user", "value": "fake-user"}],
                })
                created.raise_for_status()
                run_id = created.json()["run"]["info"]["run_id"]
                stored = client.get(api + "/runs/get", params={"run_id": run_id})
                stored.raise_for_status()
                run = stored.json()["run"]
                assert run["info"]["user_id"] == "alice@example.com", run
                assert {tag["key"]: tag["value"] for tag in run["data"]["tags"]}["mlflow.user"] == "alice@example.com"
                for action in ("set-tag", "delete-tag"):
                    assert client.post(api + "/runs/" + action, json={
                        "run_id": run_id, "key": "mlflow.user", "value": "fake-user",
                    }).status_code == 403
                assert client.post(api + "/runs/log-batch", json={
                    "run_id": run_id, "tags": [{"key": "mlflow.user", "value": "fake-user"}],
                }).status_code == 403
                client.post(api + "/runs/log-batch", json={
                    "run_id": run_id, "tags": [{"key": "training.status", "value": "done"}],
                }).raise_for_status()
                blob = b"\x00\xff\x01triton-model-artifact"
                artifact = base + "/api/2.0/mlflow-artifacts/artifacts/smoke/model.onnx"
                client.put(artifact, content=blob).raise_for_status()
                downloaded = client.get(artifact)
                downloaded.raise_for_status()
                assert downloaded.content == blob
                logged = client.post(api + "/logged-models", json={
                    "experiment_id": experiment.json()["experiment_id"], "name": "checkpoint",
                    "source_run_id": run_id, "tags": [{"key": "mlflow.user", "value": "fake-user"}],
                })
                logged.raise_for_status()
                info = logged.json()["model"]["info"]
                tags = {tag["key"]: tag["value"] for tag in info["tags"]}
                assert tags["mlflow.user"] == "alice@example.com"
                assert not isinstance(info.get("creator_id"), str)
                # An unmodified MLflow SDK must inherit identity automatically.
                inspect = json.loads(docker("inspect", server))[0]
                bridge = next(iter(inspect["NetworkSettings"]["Networks"].values()))["Gateway"]
                listener = socket.socket()
                listener.bind((bridge, 0))
                api_port = listener.getsockname()[1]
                listener.close()
                api_server = uvicorn.Server(uvicorn.Config(app, host=bridge, port=api_port, log_level="error"))
                thread = threading.Thread(target=api_server.run, daemon=True)
                thread.start()
                try:
                    started_deadline = time.monotonic() + 10
                    while not api_server.started and time.monotonic() < started_deadline:
                        time.sleep(0.05)
                    assert api_server.started
                    sdk = """import mlflow
import mlflow.sklearn
from sklearn.dummy import DummyClassifier
mlflow.set_experiment('sdk-attribution-smoke')
with mlflow.start_run() as run:
    mlflow.log_metric('accuracy', 1.0)
    model = DummyClassifier().fit([[1.0], [2.0]], [0, 1])
    logged = mlflow.sklearn.log_model(model, name='checkpoint', registered_model_name='sdk-iris',
                                    pip_requirements=['scikit-learn'])
    actual = mlflow.MlflowClient().get_run(run.info.run_id)
    assert actual.data.tags['mlflow.user'] == 'alice@example.com'
    version = mlflow.MlflowClient().get_model_version('sdk-iris', logged.registered_model_version)
    assert version.user_id == 'alice@example.com'
print('Unmodified SDK: training metrics, model upload and registration passed.')
"""
                    print(docker("exec", "-e", "PYTHONPATH=/tmp/packages", "-e",
                                 f"MLFLOW_TRACKING_URI=http://{bridge}:{api_port}{base}", "-e",
                                 "MLFLOW_TRACKING_TOKEN=fixture-workload-token", server, "python", "-c", sdk))
                finally:
                    api_server.should_exit = True
                    thread.join(timeout=5)
            print("PASS: direct access rejected; creators enforced; edits rejected; artifacts round-trip.")
        except Exception:
            for name in (server, gateway):
                print(docker("logs", "--tail", "40", name))
            raise
        finally:
            for name in (gateway, server):
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)  # noqa: S603


if __name__ == "__main__":
    main()
