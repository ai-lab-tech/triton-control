"""Render one bounded, pod-local candidate and evaluator from approved inputs."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.canonical import canonical_bytes
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, StorageBinding
from app.schemas.serving_metadata import Name
from app.schemas.validation import EvaluatorIdentity, ValidationManifest
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.kubernetes import serving_manifests
from app.services.validation import document_digest


class Inputs(BaseModel):
    """Operator-controlled, task-specific source bindings; never accepted in the operation payload."""

    model_config = ConfigDict(extra="forbid")
    evaluator: EvaluatorIdentity
    claim: Name
    plan: MaterializationPlan
    policy: MaterializerPolicy
    storage: StorageBinding


# Runs only in the qualified helper image. Copy verified bytes to a private
# volume so later writes to the source PVC cannot change evaluator inputs.
STAGE = """
import hashlib, json, sys
from pathlib import Path
total = 0
for argument in sys.argv[1:]:
    name, expected = argument.split("=", 1)
    checksum = hashlib.sha256()
    dest = Path("/verified") / name
    with open("/source/" + name, "rb") as src, dest.open("wb") as out:
        while chunk := src.read(1024 * 1024):
            total += len(chunk)
            if total > 10 * 1024**3:
                sys.exit("validation input budget exceeded")
            checksum.update(chunk)
            out.write(chunk)
    if checksum.hexdigest() != expected:
        sys.exit("validation input digest mismatch")
startup = json.loads(Path("/plan/manifest.json").read_bytes())["startup"]
expected = {key: startup[key] for key in ("models", "resourcesPerReplica")}
if json.loads(Path("/verified/startup").read_bytes()) != expected:
    sys.exit("startup configuration mismatch")
"""


# This launcher is part of the owned Job; only the evaluator container imports project code.
RUN = """
import runpy
from pathlib import Path
from app.schemas.validation import ValidationManifest
manifest = ValidationManifest.model_validate_json(Path("/plan/manifest.json").read_bytes())
report = runpy.run_path("/verified/evaluator.py")["evaluate"](manifest)
print(report.model_dump_json(by_alias=True))
"""


def render(
    manifest: ValidationManifest, inputs: Inputs, name: str, namespace: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = document_digest(manifest)
    if manifest.evaluator != inputs.evaluator:
        raise ValueError("Evaluator differs from trusted task binding")
    config, pod = serving_manifests(
        inputs.plan,
        inputs.policy,
        inputs.storage,
        name=name,
        namespace=namespace,
        limits=CaptureLimits(),
        identity_digest=identity,
        subject="validation-manifest",
        image=manifest.image,
        repository=manifest.repository,
        models=manifest.startup.models,
        resources=manifest.startup.resources_per_replica,
    )
    config["data"]["manifest.json"] = canonical_bytes(manifest.model_dump(mode="json", by_alias=True)).decode()
    if sum(len(value.encode()) for value in config["data"].values()) > 950 * 1024:
        raise ValueError("Validation configuration exceeds size budget")
    spec = pod["spec"]
    server, readiness = spec["containers"]
    # Native Job sidecars start in order. Gate the evaluator on the same exact
    # receipt/model-version probe used for production readiness.
    server["restartPolicy"] = readiness["restartPolicy"] = "Always"
    readiness["startupProbe"] = {**readiness["readinessProbe"], "failureThreshold": 360}
    artifacts = {
        "dataset": manifest.dataset,
        "expected": manifest.expected_outputs,
        "evaluator.py": manifest.evaluator.code,
    }
    artifacts["startup"] = manifest.startup.configuration
    if manifest.source_code:
        artifacts["source"] = manifest.source_code
    mounts = [
        {"name": "inputs", "mountPath": "/source/" + key, "subPath": ref.digest.split(":")[1], "readOnly": True}
        for key, ref in artifacts.items()
    ]
    resources = {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "1Gi"}}
    stage: dict[str, Any] = {
        "name": "verify-inputs",
        "image": inputs.policy.image,
        "command": ["python", "-I", "-c", STAGE],
        "args": [key + "=" + ref.digest.split(":")[1] for key, ref in artifacts.items()],
        "volumeMounts": [
            *mounts,
            {"name": "verified", "mountPath": "/verified"},
            {"name": "materialization-plan", "mountPath": "/plan", "readOnly": True},
        ],
        "securityContext": readiness["securityContext"],
        "resources": resources,
    }
    spec["initContainers"].extend([stage, server, readiness])
    spec["volumes"].extend(
        [
            {"name": "inputs", "persistentVolumeClaim": {"claimName": inputs.claim, "readOnly": True}},
            {"name": "verified", "emptyDir": {"sizeLimit": "10Gi"}},
        ]
    )
    spec["containers"] = [
        {
            "name": "evaluator",
            "image": manifest.evaluator.image,
            "command": ["python", "-I", "-B", "-c", RUN],
            "env": [{"name": "TRITON_VALIDATION_ENDPOINT", "value": "http://127.0.0.1:8000"}],
            "securityContext": readiness["securityContext"],
            "resources": resources,
            "volumeMounts": [
                {"name": "verified", "mountPath": "/verified", "readOnly": True},
                {
                    "name": "materialization-plan",
                    "mountPath": "/plan/manifest.json",
                    "subPath": "manifest.json",
                    "readOnly": True,
                },
            ],
        }
    ]
    spec["restartPolicy"] = "Never"
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "activeDeadlineSeconds": 3600,
            "template": {
                "metadata": {
                    "labels": {"triton-control/workload": "candidate-validation"},
                    "annotations": pod["metadata"]["annotations"],
                },
                "spec": spec,
            },
        },
    }
    return config, job
