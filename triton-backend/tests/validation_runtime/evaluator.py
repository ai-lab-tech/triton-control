"""CPU fixture evaluator; executed exclusively inside the validation Job."""

import hashlib
import json
import platform
import time
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

from app.core.canonical import digest
from app.schemas.validation import ValidationReport


def evaluate(manifest):
    started = datetime.now(timezone.utc)
    connection = HTTPConnection("127.0.0.1", 8000, timeout=5)
    connection.request("GET", "/v2")
    version = json.load(connection.getresponse())["version"]
    data = json.loads(Path("/verified/dataset").read_bytes())
    expected = json.loads(Path("/verified/expected").read_bytes())
    start = time.monotonic()
    connection.request("POST", "/v2/models/iris/versions/1/infer", json.dumps({"inputs": [{"data": [data]}]}))
    prediction = json.load(connection.getresponse())["outputs"][0]["data"][0]
    elapsed_ms = (time.monotonic() - start) * 1000
    connection.close()
    architecture = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    cpu = next(
        line.split(":", 1)[1].strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith(("model name", "CPU part"))
    )
    profile = dict(
        architecture=architecture,
        operatingSystem="linux",
        cpuModel=hashlib.sha256(cpu.encode()).hexdigest(),
        gpuModel=None,
        gpuCount=0,
        driverVersion=None,
        cudaVersion=None,
        tritonVersion=version,
    )
    identity = digest(manifest.model_dump(by_alias=True, mode="json"))
    values = {"accuracy": str(int(prediction == expected)), "latency": f"{elapsed_ms:.6f}"}
    metrics = [dict(criterion=c, value=values[c.metric], passed=c.accepts(values[c.metric])) for c in manifest.criteria]
    status = "passed" if all(metric["passed"] for metric in metrics) else "failed"
    errors = []
    if profile not in [target.model_dump(by_alias=True) for target in manifest.supported_targets]:
        status, metrics, errors = "unsupported", [], [{"code": "unsupported-target"}]
    return ValidationReport.model_validate(
        dict(
            apiVersion=manifest.api_version,
            kind="ValidationReport",
            manifestDigest=identity,
            evaluator=manifest.evaluator,
            subjects=dict(
                image=manifest.image.split("@")[1],
                inventoryDigest=manifest.repository.inventory_digest,
                preparedInventoryDigest=manifest.repository.prepared_inventory_digest,
                materializerImage=manifest.repository.materializer_image.split("@")[1],
                validationInput=identity,
            ),
            measuredTarget=profile,
            startedAt=started,
            completedAt=datetime.now(timezone.utc),
            status=status,
            metrics=metrics,
            errors=errors,
        )
    )
