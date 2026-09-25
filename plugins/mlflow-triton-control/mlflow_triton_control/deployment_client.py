"""MLflow deployment client backed by Triton Control's deployment API."""

from __future__ import annotations

import os
import re
import time
from urllib.parse import quote, urlsplit

import httpx
from mlflow.deployments import BaseDeploymentClient
from mlflow.exceptions import MlflowException

_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")


def target_help() -> str:
    return (
        "Target: triton-control://<triton-control-host>[:port]. "
        "Install this package in the MLflow CLI environment. "
        "Use -m s3://<profile-bucket>/<repository-prefix>/<model-name> and "
        "-C s3_profile_id=<owned-profile-id> -C image=<triton-image>. "
        "The model must already exist in Triton repository format. "
        "Set TRITON_CONTROL_TOKEN to a user-bound API token. "
        "MLflow models:/ and runs:/ artifacts, update, predict and local serving are unsupported."
    )


def run_local(target, name, model_uri, flavor=None, config=None):
    raise MlflowException("Local Triton serving is not supported by triton-control")


class TritonControlDeploymentClient(BaseDeploymentClient):
    """Create and manage user-owned Triton Control deployments."""

    def __init__(self, target_uri):
        super().__init__(target_uri)
        parsed = urlsplit(target_uri)
        if parsed.scheme != "triton-control" or not parsed.netloc or parsed.path not in {"", "/"}:
            raise MlflowException("Target URI must be triton-control://<host>[:port]")
        self._base_url = f"http://{parsed.netloc}"

    def _request(self, method, path, **kwargs):
        token = os.getenv("TRITON_CONTROL_TOKEN", "").strip()
        if not token:
            raise MlflowException("TRITON_CONTROL_TOKEN is required")
        try:
            with httpx.Client(base_url=self._base_url, timeout=10.0, follow_redirects=False) as client:
                response = client.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", "Request failed")
            except (ValueError, AttributeError):
                detail = "Request failed"
            raise MlflowException(f"Triton Control returned HTTP {exc.response.status_code}: {detail}") from exc
        except httpx.RequestError as exc:
            raise MlflowException(f"Triton Control is unavailable: {type(exc).__name__}") from exc

    def create_deployment(self, name, model_uri, flavor=None, config=None, endpoint=None):
        if endpoint is not None:
            raise MlflowException("endpoint is not supported")
        if flavor not in {None, "triton"}:
            raise MlflowException("Only the triton flavor is supported")
        if not _NAME.fullmatch(name):
            raise MlflowException("name must be a lowercase Kubernetes deployment name")
        config = config or {}
        try:
            profile_id = int(config["s3_profile_id"])
            image = str(config["image"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise MlflowException("s3_profile_id and image are required") from exc
        if profile_id <= 0 or not image:
            raise MlflowException("s3_profile_id must be positive and image must not be empty")
        try:
            timeout = float(config.get("wait_timeout_seconds", 300))
        except (TypeError, ValueError) as exc:
            raise MlflowException("wait_timeout_seconds must be a positive number") from exc
        if timeout <= 0:
            raise MlflowException("wait_timeout_seconds must be a positive number")
        uri = urlsplit(model_uri)
        parts = uri.path.strip("/").split("/")
        if (uri.scheme != "s3" or not uri.netloc or uri.query or uri.fragment
                or len(parts) < 2 or any(part in {"", ".", ".."} for part in parts)
                or not _MODEL.fullmatch(parts[-1])):
            raise MlflowException("model_uri must be s3://<bucket>/<repository-prefix>/<model-name>")
        prefix, model_name = "/".join(parts[:-1]), parts[-1]
        payload = {
            "deployment_name": name,
            "s3_profile_id": profile_id,
            "model_uri": model_uri,
            "repository_prefix": prefix,
            "model_name": model_name,
            "image": image,
            "model_control_mode": "explicit",
        }
        result = self._request("POST", "/api/deployments", json=payload)
        deadline = time.monotonic() + timeout
        while True:
            current = self.get_deployment(name)
            if current["status"] == "ready":
                return current
            if time.monotonic() >= deadline:
                raise MlflowException(
                    f"Deployment {name} (instance {result.get('instance_id')}) did not become ready: "
                    f"{current.get('status_detail', current['status'])}"
                )
            time.sleep(min(3.0, max(0.0, deadline - time.monotonic())))

    def get_deployment(self, name):
        return self._request("GET", f"/api/deployments/mlflow/{quote(name, safe='')}")

    def list_deployments(self):
        return self._request("GET", "/api/deployments/mlflow")

    def delete_deployment(self, name):
        return self._request("DELETE", f"/api/deployments/mlflow/{quote(name, safe='')}")

    def update_deployment(self, name, model_uri=None, flavor=None, config=None, endpoint=None):
        raise MlflowException("Updating deployments is not supported")

    def predict(self, deployment_name=None, inputs=None, endpoint=None):
        raise MlflowException("Prediction is not supported")
