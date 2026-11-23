"""Carry the live model selection into replicas of the existing Triton workload."""

import json
import re
import shlex
from copy import deepcopy
from typing import Any

import httpx

from app.exceptions import ConflictError
from app.schemas.serving_metadata import StartupModel

# Executed inside the existing Triton image. A pod enters service only after
# Triton reports the same ready model/version pairs as the original pod.
READINESS = """import http.client, json, sys
client = http.client.HTTPConnection("127.0.0.1", 8000, timeout=2)
client.request("GET", "/v2/health/ready")
response = client.getresponse()
response.read()
if response.status != 200:
    sys.exit(1)
client.request("POST", "/v2/repository/index", '{"ready":true}', {"Content-Type": "application/json"})
response = client.getresponse()
if response.status != 200:
    sys.exit(1)
ready = sorted((r["name"], r["version"]) for r in json.load(response) if r["state"] == "READY")
sys.exit(ready != sorted(tuple(pair) for pair in json.loads(sys.argv[1])))
"""


def ready_models(endpoint: str) -> tuple[StartupModel, ...]:
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            response = client.post(endpoint + "/v2/repository/index", json={"ready": True})
            response.raise_for_status()
            versions: dict[str, set[str]] = {}
            for row in response.json():
                if row["state"] == "READY":
                    versions.setdefault(row["name"], set()).add(row["version"])
            models = tuple(
                StartupModel(name=name, versions=tuple(sorted(values))) for name, values in sorted(versions.items())
            )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ConflictError("Could not read ready model versions from the serving pod") from error
    if not models or len(models) > 100:
        raise ConflictError("Scaling requires between one and 100 ready models")
    return models


def launch_arguments(runtime: dict[str, Any]) -> tuple[str, list[str]]:
    args = runtime.get("args", [])
    shell = runtime.get("command") == ["/bin/bash", "-c"]
    prefix = ""
    if shell:
        launch = re.search(r"(?m)^exec tritonserver[ \t]+", args[0]) if len(args) == 1 else None
        if launch is None:
            return "", []
        prefix = args[0][:launch.end()]
        try:
            args = shlex.split(args[0][launch.end():])
        except ValueError:
            return "", []
    return prefix, args


def serving_template(template: dict[str, Any], models: tuple[StartupModel, ...]) -> dict[str, Any]:
    result = deepcopy(template)
    runtime = next(c for c in result["spec"]["containers"] if c["name"] == "triton")
    prefix, args = launch_arguments(runtime)
    if "--model-control-mode=explicit" not in args:
        raise ConflictError("Scaling the loaded models requires explicit model control")
    args = [arg for arg in args if not arg.startswith("--load-model=")]
    args.extend("--load-model=" + model.name for model in models)
    runtime["args"] = [prefix + shlex.join(args)] if prefix else args

    pairs = json.dumps([(m.name, v) for m in models for v in m.versions], separators=(",", ":"))
    if len(pairs) > 100_000:
        raise ConflictError("The loaded model selection exceeds the Kubernetes probe argument limit")
    probe = runtime.setdefault("readinessProbe", {})
    for handler in ("httpGet", "tcpSocket", "grpc"):
        probe.pop(handler, None)
    probe["exec"] = {"command": ["python3", "-c", READINESS, pairs]}
    probe["timeoutSeconds"] = max(6, probe.get("timeoutSeconds", 1))
    return result
