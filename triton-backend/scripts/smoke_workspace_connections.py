"""Read-only smoke test inside a managed code-server workspace (stdlib only).

From the repository root:
kubectl exec -i -n triton-control <workspace-pod> -c code-server -- python3 - \
    < triton-backend/scripts/smoke_workspace_connections.py
"""

from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def check(name: str, url: str, token: str, collection: str) -> None:
    # Arrange: use only the credentials injected into this workspace.
    request = Request(url, headers={"Authorization": f"Bearer {token}"})

    # Act: query the real service through Triton Control.
    with urlopen(request, timeout=30) as response:  # nosec B310 - trusted workspace integration URL
        status = response.status
        payload = json.load(response)

    # Assert: HTTP success and a valid service response, including empty lists.
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"{name}: expected HTTP 200 and a JSON object")
    items = payload.get(collection) or []
    if not isinstance(items, list):
        raise RuntimeError(f"{name}: invalid {collection} response")
    print(f"PASS {name}: HTTP 200 ({len(items)} {collection})")

    # Assert: the same endpoint rejects missing and incorrect credentials.
    for label, headers in (
        ("missing", {}),
        ("incorrect", {"Authorization": "Bearer invalid-smoke-credential"}),
    ):
        try:
            with urlopen(Request(url, headers=headers), timeout=30):  # nosec B310
                raise RuntimeError(f"{name}: accepted {label} credentials")
        except HTTPError as exc:
            if exc.code not in {401, 403}:
                raise RuntimeError(f"{name}: unexpected HTTP {exc.code} for {label} credentials") from None
        print(f"PASS {name}: {label} credentials rejected")


def main() -> int:
    required = (
        "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN",
        "TRITON_CONTROL_ARGO_URL", "TRITON_CONTROL_ARGO_TOKEN",
    )
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        print("FAIL: missing injected environment variables: " + ", ".join(missing), file=sys.stderr)
        return 1
    checks = (
        ("MLflow", os.environ["MLFLOW_TRACKING_URI"].rstrip("/")
         + "/api/2.0/mlflow/experiments/search?max_results=100",
         os.environ["MLFLOW_TRACKING_TOKEN"], "experiments"),
        ("Argo", os.environ["TRITON_CONTROL_ARGO_URL"], os.environ["TRITON_CONTROL_ARGO_TOKEN"], "items"),
    )
    failed = False
    for name, url, token, collection in checks:
        try:
            check(name, url, token, collection)
        except HTTPError as exc:
            print(f"FAIL {name}: HTTP {exc.code}", file=sys.stderr)
            failed = True
        except (URLError, TimeoutError, ValueError, RuntimeError):
            # Never print request headers, tokens, or potentially sensitive bodies.
            print(f"FAIL {name}: connection, authentication, or response check failed", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
