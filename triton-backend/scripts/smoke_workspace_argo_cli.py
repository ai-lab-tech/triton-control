"""Exercise the real Argo CLI with injected workspace credentials in CI."""

import json
import os
import subprocess
import sys
from typing import Any


def run(*args: str, source: str | None = None, token: str | None = None) -> dict[str, Any] | list[dict[str, Any]]:
    environment = dict(os.environ)
    if token is not None:
        environment["ARGO_TOKEN"] = token
    result = subprocess.run(
        ["argo", *args], input=source, text=True, capture_output=True, env=environment, timeout=60, check=False,
    )
    if token is not None:
        if result.returncode == 0:
            raise RuntimeError("CLI accepted an invalid credential")
        return {}
    if result.returncode:
        # CLI diagnostics can include submitted content: never echo them into CI logs.
        raise RuntimeError("Argo CLI request failed")
    payload: object = json.loads(result.stdout)
    if isinstance(payload, dict) or isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise RuntimeError("Argo CLI returned an invalid JSON response")


def workflow_name(payload: dict[str, Any] | list[dict[str, Any]]) -> str:
    if isinstance(payload, dict):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            name = metadata.get("name")
            if isinstance(name, str) and name:
                return name
    raise RuntimeError("Argo CLI returned an invalid workflow identity")


def main() -> None:
    run("list", "-o", "json")
    run("list", "-o", "json", token="")
    run("list", "-o", "json", token="Bearer invalid-smoke-token")
    workflow = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Workflow", "metadata": {
        "generateName": "cli-smoke-",
    }, "spec": {"entrypoint": "main", "templates": [{"name": "main", "container": {
        "image": "python:3.12-slim", "command": ["python", "-c"], "args": ["print(42)"],
    }}]}}
    created = run("submit", "-", "-o", "json", source=json.dumps(workflow))
    name = workflow_name(created)
    try:
        listed = run("list", "-o", "json")
        items = listed if isinstance(listed, list) else listed.get("items") or []
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise RuntimeError("Argo CLI returned an invalid workflow list")
        if not any(workflow_name(item) == name for item in items):
            raise RuntimeError("CLI list omitted the owned workflow")
        fetched = run("get", name, "-o", "json")
        if workflow_name(fetched) != name:
            raise RuntimeError("CLI returned the wrong workflow")
    finally:
        # Delete prints text; use a separate call without JSON decoding.
        deleted = subprocess.run(["argo", "delete", name], capture_output=True, timeout=60, check=False)
        if deleted.returncode:
            raise RuntimeError("CLI workflow cleanup failed")
    print("PASS Argo CLI: list, submit, get, delete; missing and invalid credentials rejected")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("FAIL Argo CLI smoke test; inspect backend logs", file=sys.stderr)
        sys.exit(1)
