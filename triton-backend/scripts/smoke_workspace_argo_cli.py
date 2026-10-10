"""Exercise the real Argo CLI with injected workspace credentials in CI."""

import json
import os
import subprocess
import sys


def run(*args: str, source: str | None = None, token: str | None = None) -> dict | list:
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
    return json.loads(result.stdout)


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
    name = created["metadata"]["name"]
    try:
        listed = run("list", "-o", "json")
        items = listed if isinstance(listed, list) else listed.get("items") or []
        if not any(item["metadata"]["name"] == name for item in items):
            raise RuntimeError("CLI list omitted the owned workflow")
        fetched = run("get", name, "-o", "json")
        if fetched["metadata"]["name"] != name:
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
