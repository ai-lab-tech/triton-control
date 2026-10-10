"""Create CI fixtures through the real Triton Control API; print only the pod name."""

import json
import secrets
import sys
import time
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


def main() -> None:
    base = "http://127.0.0.1:18000"
    token = ""

    def request(path: str, payload: dict[str, Any] | None = None) -> Any:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request(base + path, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
        with urlopen(req, timeout=900) as response:  # nosec B310 - local CI port-forward
            return json.load(response)

    for _ in range(60):
        try:
            request("/health")
            break
        except (URLError, TimeoutError):
            time.sleep(1)
    else:
        raise RuntimeError("Backend port-forward did not become ready")

    password = secrets.token_urlsafe(24) + "Aa1!"
    print("Creating the CI user", file=sys.stderr)
    request("/api/auth/bootstrap/register", {"email": "smoke@example.test", "password": password, "name": "CI Smoke"})
    token = request("/api/auth/login", {"email": "smoke@example.test", "password": password})["access_token"]
    print("Installing MLflow", file=sys.stderr)
    request("/api/mlflow", {"installation_name": "mlflow"})
    print("Creating the code-server workspace", file=sys.stderr)
    workspace = request("/api/development", {
        "name": "smoke", "image": "triton-workspace-smoke:ci", "image_has_code_server": True,
        "storage_size": "1Gi", "gpu_count": 0, "memory": "256Mi", "memory_limit": "1Gi",
    })
    print(workspace["statefulset_name"] + "-0")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Avoid printing response bodies or login credentials in CI logs.
        print("CI fixture setup failed; inspect pod status and backend logs", file=sys.stderr)
        sys.exit(1)
