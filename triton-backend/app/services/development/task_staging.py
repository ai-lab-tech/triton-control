"""Kubernetes input isolation and a credential-free, verified task manifest."""

import json
import os
import shlex
from pathlib import Path
from typing import Any

from app.services.mlflow.tracking import control_url


def configmap_name(name: str) -> str:
    return name[:50].rstrip("-") + "-task-inputs"


def configure(manifests: list[dict[str, Any]], document: dict[str, Any], namespace: str,
              name: str, secret_name: str) -> None:
    statefulset = next(item for item in manifests if item["kind"] == "StatefulSet")
    spec = statefulset["spec"]["template"]["spec"]
    container = spec["containers"][0]
    container["volumeMounts"].append({"name": "task-inputs", "mountPath": "/inputs", "readOnly": True})
    # Even an image running as root must not remount the immutable input volume.
    container["securityContext"] = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    folders = json.dumps({"folders": [
        {"path": "/inputs/verified", "name": "Verified inputs (read-only)"},
        {"path": "/workspace/conversion", "name": "Conversion (writable)"},
    ]})
    container["args"][0] = (
        "mkdir -p /workspace/conversion /workspace/.triton-control; "
        + "printf '%s' " + shlex.quote(folders) + " > /workspace/.triton-control/workspace.code-workspace; "
        + container["args"][0]
    )
    spec["automountServiceAccountToken"] = False
    spec["volumes"].extend([
        {"name": "task-inputs", "emptyDir": {"sizeLimit": "4Gi"}},
        {"name": "task-stager", "configMap": {"name": configmap_name(name)}},
    ])
    spec["initContainers"] = [{
        "name": "stage-task-inputs",
        # The user-selected editor image must not supply the verifier/interpreter.
        "image": os.getenv("TASK_WORKSPACE_STAGING_IMAGE", "python:3.12-alpine"),
        "command": ["python3", "/stager/stage.py"],
        "env": [
            {"name": "TASK_INPUT_URL", "value": control_url() +
             f"/api/preparation/workspace-inputs/{namespace}/{name}"},
            {"name": "TASK_INPUT_TOKEN", "valueFrom": {"secretKeyRef": {
                "name": secret_name, "key": "S3_PROFILE_TOKEN",
            }}},
        ],
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
        "resources": {"requests": {"cpu": "100m", "memory": "64Mi"},
                      "limits": {"cpu": "1", "memory": "256Mi"}},
        "volumeMounts": [
            {"name": "task-inputs", "mountPath": "/inputs"},
            {"name": "task-stager", "mountPath": "/stager", "readOnly": True},
        ],
    }]
    manifests.insert(0, {
        "apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": configmap_name(name), "namespace": namespace},
        "data": {"stage.py": Path(__file__).with_name("task_stage_runner.py").read_text(),
                 "task.json": json.dumps(document, sort_keys=True)},
    })
