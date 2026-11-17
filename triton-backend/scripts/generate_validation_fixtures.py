"""Reproducible, synthetic/non-deployable S07-A fixtures and hash vectors."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.validation import API_VERSION, EvidenceEnvelope, ValidationManifest, ValidationReport  # noqa: E402
from app.services.lineage import canonical_bytes, digest  # noqa: E402
from app.services.validation import artifact_subjects, document_digest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts/validation/v1alpha1/fixtures"
SERVING = ROOT / "contracts/serving/v1alpha1/fixtures"


def generate() -> dict[str, bytes]:
    artifacts = {p.name: p.read_bytes() for p in (SERVING / "artifacts").iterdir() if p.is_file()}
    artifacts.update({
        "dataset.txt": b"SYNTHETIC evaluation inputs: iris fixture, no real measurements\n",
        "expected.txt": b"SYNTHETIC expected classes\n",
        "evaluator.txt": b"SYNTHETIC evaluator source descriptor; not executable\n",
        "evaluator-image.txt": b"SYNTHETIC image descriptor; not a registry image\n",
        "startup.txt": b"SYNTHETIC startup configuration descriptor\n",
    })

    def ref(name: str) -> dict[str, str]:
        return {"uri": "artifacts/" + name, "digest": "sha256:" + hashlib.sha256(artifacts[name]).hexdigest()}

    release = json.loads((SERVING / "release-manual.json").read_bytes())
    spec = release["spec"]
    profile = {
        "architecture": "amd64", "operatingSystem": "linux", "cpuModel": "synthetic-cpu",
        "gpuModel": None, "gpuCount": 0, "driverVersion": None, "cudaVersion": None,
        "tritonVersion": "synthetic-1",
    }
    manifest = ValidationManifest.model_validate({
        "apiVersion": API_VERSION, "kind": "ValidationManifest", "image": spec["image"],
        "repository": spec["repository"], "dataset": ref("dataset.txt"),
        "expectedOutputs": ref("expected.txt"), "sourceCode": ref("evaluator.txt"),
        "evaluator": {"name": "synthetic-evaluator", "version": "1.0",
                      "image": "example.invalid/evaluator@" + ref("evaluator-image.txt")["digest"],
                      "code": ref("evaluator.txt")},
        "criteria": [{"metric": "accuracy", "category": "correctness", "unit": "ratio",
                      "operator": "gte", "threshold": "0.95"},
                     {"metric": "latency-p95", "category": "performance", "unit": "milliseconds",
                      "operator": "lte", "threshold": "20"}],
        "startup": {"models": spec["models"], "configuration": ref("startup.txt"),
                    "resourcesPerReplica": spec["resourcesPerReplica"]},
        "supportedTargets": [profile],
    })
    subjects = artifact_subjects(manifest).model_dump(mode="json", by_alias=True)
    report_data = {
        "apiVersion": API_VERSION, "kind": "ValidationReport", "manifestDigest": document_digest(manifest),
        "evaluator": manifest.evaluator.model_dump(mode="json", by_alias=True), "subjects": subjects,
        "measuredTarget": profile, "startedAt": "2026-01-01T00:00:00Z", "completedAt": "2026-01-01T00:01:00Z",
        "status": "passed", "errors": [], "metrics": [
            {"criterion": c.model_dump(mode="json", by_alias=True), "value": value, "passed": True}
            for c, value in zip(manifest.criteria, ("0.97", "12"), strict=True)
        ],
    }
    report = ValidationReport.model_validate(report_data)
    envelope = EvidenceEnvelope.model_validate({
        "apiVersion": API_VERSION, "kind": "EvidenceEnvelope", "issuer": "synthetic-untrusted-issuer",
        "policy": {"name": "synthetic-policy", "version": "1"},
        "tools": [{"name": manifest.evaluator.name, "version": manifest.evaluator.version}],
        "subjects": subjects, "report": {"uri": "report-passed.json", "digest": document_digest(report)},
        "issuedAt": "2026-01-01T00:02:00Z", "validUntil": "2026-01-02T00:00:00Z",
    })
    unsupported = ValidationReport.model_validate({
        **report_data, "status": "unsupported", "metrics": [],
        "errors": [{"code": "unsupported-target"}], "measuredTarget": {**profile, "architecture": "arm64"},
    })
    interrupted = ValidationReport.model_validate({
        **report_data, "status": "error", "metrics": [], "measuredTarget": None,
        "errors": [{"code": "runner-failure"}],
    })
    failed_data = json.loads(report.model_dump_json(by_alias=True))
    failed_data["status"] = "failed"
    failed_data["metrics"][0].update(value="0.90", passed=False)
    failed = ValidationReport.model_validate(failed_data)
    spec["validation"] = {"uri": "evidence.json", "digest": document_digest(envelope), "subjects": subjects}
    release_hash = digest(release)
    target = json.loads((SERVING / "target.json").read_bytes())
    target["spec"]["release"] = {"uri": "release.json", "digest": release_hash}
    scaled = json.loads(json.dumps(target))
    scaled["spec"]["replicas"] = target["spec"]["replicas"] + 2
    documents: dict[str, Any] = {
        "manifest": manifest.model_dump(mode="json", by_alias=True),
        "report-passed": report.model_dump(mode="json", by_alias=True),
        "report-failed": failed.model_dump(mode="json", by_alias=True),
        "report-error": interrupted.model_dump(mode="json", by_alias=True),
        "report-unsupported": unsupported.model_dump(mode="json", by_alias=True),
        "evidence": envelope.model_dump(mode="json", by_alias=True),
        "release": release, "target": target, "target-scaled": scaled,
    }
    output = {"artifacts/" + name: raw for name, raw in artifacts.items()}
    hashes = {}
    for name, value in documents.items():
        output[name + ".json"] = (json.dumps(value, indent=2) + "\n").encode()
        output[name + ".yaml"] = yaml.safe_dump(value, sort_keys=False).encode()
        output["canonical/" + name + ".json"] = canonical_bytes(value)
        hashes[name] = digest(value)
    output["hashes.json"] = (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode()
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name, raw in generate().items():
        path = FIXTURES / name
        if args.check:
            if not path.is_file() or path.read_bytes() != raw:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    if stale:
        print("Stale validation fixtures: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
