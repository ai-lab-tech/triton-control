"""Reproduce synthetic, NON-DEPLOYABLE conformance fixtures from explicit bytes."""

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas.lineage import InventoryV1, ServingCandidateV1  # noqa: E402
from app.services.lineage import canonical_bytes, digest, inventory_digest  # noqa: E402
from app.services.serving_metadata import load_metadata  # noqa: E402

ROOT = Path(__file__).resolve().parents[2] / "contracts/serving/v1alpha1/fixtures"


def fixtures() -> dict[str, bytes]:
    output: dict[str, bytes] = {}

    def artifact(name: str, value: bytes) -> str:
        output[f"artifacts/{name}"] = value
        return "sha256:" + hashlib.sha256(value).hexdigest()

    def json_artifact(name: str, value: Any) -> str:
        return artifact(name, canonical_bytes(value))

    # These bytes are test descriptors, not real registry manifests or qualified images.
    image_digest = artifact("image.txt", b"SYNTHETIC image descriptor: runtime one\n")
    second_image = artifact("image-two.txt", b"SYNTHETIC image descriptor: runtime two\n")
    weight = artifact("weights.txt", b"SYNTHETIC model weights\n")
    # Derive size from actual bytes, not from an assumed external artifact.
    inventory = InventoryV1.model_validate(
        {
            "files": [
                {
                    "path": "toy/1/weights.txt",
                    "size": len(output["artifacts/weights.txt"]),
                    "digest": weight,
                }
            ]
        }
    )
    inventory_id = inventory_digest(inventory)
    json_artifact("inventory.json", inventory.model_dump(mode="json"))
    origin = {"installation_id": "synthetic-fixture", "record_id": "candidate-one"}
    candidate = ServingCandidateV1.model_validate(
        {
            "origin": origin,
            "derived_from_snapshot": {
                "origin": {"installation_id": "synthetic-fixture", "record_id": "snapshot-one"},
                "kind": "manual",
                "inventory": inventory_id,
            },
            "attempts": [
                {
                    "origin": {"installation_id": "synthetic-fixture", "record_id": "attempt-one"},
                    "operation": "packaging",
                    "input_inventory": inventory_id,
                    "output_inventory": inventory_id,
                }
            ],
            "output_inventory": inventory_id,
        }
    )
    candidate_id = json_artifact("candidate.json", candidate.model_dump(mode="json"))
    api = "serving.triton-control/v1alpha1"
    image = f"registry.example.invalid/synthetic/runtime@{image_digest}"
    lineage = {
        "schema_version": 1,
        "origin": {"installation_id": "synthetic-fixture", "record_id": "release-one"},
        "candidate": origin,
        "candidate_digest": candidate_id,
        "runtime_image": image,
        "replaces_release": None,
    }
    lineage_id = json_artifact("lineage.json", lineage)
    subjects = {"image": image_digest, "inventoryDigest": inventory_id, "preparedInventoryDigest": inventory_id}
    evidence_id = json_artifact("validation.json", {"synthetic": True, "subjects": subjects})
    release: dict[str, Any] = {
        "apiVersion": api,
        "kind": "ServingRelease",
        "metadata": {"name": "toy", "release": "r1"},
        "spec": {
            "image": image,
            "repository": {
                "uri": "s3://synthetic-models/toy/r1/",
                "inventoryDigest": inventory_id,
                "preparedInventoryDigest": inventory_id,
            },
            "models": [{"name": "toy", "versions": ["1"]}],
            "resourcesPerReplica": {
                "requests": {"cpu": "250m", "memory": "128Mi"},
                "limits": {"cpu": "1", "memory": "256Mi"},
                "gpuCount": 0,
            },
            "lineage": lineage,
            "lineageManifest": {"uri": "artifacts/lineage.json", "digest": lineage_id},
            "validation": {"uri": "artifacts/validation.json", "digest": evidence_id, "subjects": subjects},
        },
    }
    vectors: dict[str, Any] = {"lineageV1": {"candidate": candidate_id, "inventory": inventory_id}, "documents": {}}

    def document(name: str, value: dict[str, Any]) -> str:
        raw = (json.dumps(value, indent=2) + "\n").encode()
        loaded = load_metadata(raw)
        output[f"{name}.json"] = raw
        output[f"{name}.yaml"] = yaml.safe_dump(value, sort_keys=False).encode()
        output[f"canonical/{name}.json"] = loaded.canonical
        vectors["documents"][name] = loaded.digest
        return loaded.digest

    release_id = document("release-manual", release)
    runtime = copy.deepcopy(release)
    runtime["metadata"]["release"] = "r2"
    runtime_spec = runtime["spec"]
    runtime_spec["image"] = f"registry.example.invalid/synthetic/runtime@{second_image}"
    runtime_spec["lineage"]["runtime_image"] = runtime_spec["image"]
    runtime_spec["lineage"]["origin"] = {"installation_id": "synthetic-fixture", "record_id": "release-two"}
    runtime_spec["lineage"]["replaces_release"] = lineage["origin"]
    runtime_spec["lineageManifest"] = {
        "uri": "artifacts/lineage-two.json",
        "digest": json_artifact("lineage-two.json", runtime_spec["lineage"]),
    }
    runtime_spec["validation"]["subjects"]["image"] = second_image
    runtime_spec["validation"]["uri"] = "artifacts/validation-two.json"
    runtime_spec["validation"]["digest"] = json_artifact(
        "validation-two.json", {"synthetic": True, "subjects": runtime_spec["validation"]["subjects"]}
    )
    document("release-runtime-only", runtime)
    security = copy.deepcopy(release)
    security["metadata"]["release"] = "r1-security"
    security_id = json_artifact("security.json", {"synthetic": True, "report": "security", "subjects": subjects})
    security["spec"]["security"] = {"uri": "artifacts/security.json", "digest": security_id, "subjects": subjects}
    document("release-security", security)
    target: dict[str, Any] = {
        "apiVersion": api,
        "kind": "ServingTarget",
        "metadata": {"name": "toy-production"},
        "spec": {
            "release": {"uri": "release-manual.json", "digest": release_id},
            "replicas": 1,
            "credentials": {"modelStorage": "production-models", "imageRegistry": "serving-registry"},
            "rollout": {"maxSurge": 0, "maxUnavailable": 1},
        },
    }
    document("target", target)
    scaled = copy.deepcopy(target)
    scaled["spec"]["replicas"] = 3
    document("target-scaled", scaled)
    source: dict[str, Any] = {
        "apiVersion": api,
        "kind": "MetadataSource",
        "metadata": {"name": "production-git"},
        "spec": {
            "location": {
                "type": "git",
                "repository": "https://git.example.invalid/team/releases.git",
                "ref": "refs/heads/main",
                "path": "targets/production.yaml",
            },
            "readCredential": "metadata-reader",
            "allowedTargets": ["toy-production"],
        },
    }
    document("source-git", source)
    source_s3 = copy.deepcopy(source)
    source_s3["metadata"]["name"] = "production-s3"
    source_s3["spec"]["location"] = {"type": "s3", "bucket": "synthetic-metadata", "key": "targets/production.json"}
    document("source-s3", source_s3)
    output["hashes.json"] = (json.dumps(vectors, indent=2, sort_keys=True) + "\n").encode()
    # Independently assert compatibility with S05-A canonical identity functions.
    assert candidate_id == digest(candidate.model_dump(mode="json"))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    stale = []
    for name, content in fixtures().items():
        path = ROOT / name
        if args.check:
            if not path.exists() or path.read_bytes() != content:
                stale.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if stale:
        print("Stale serving fixtures: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
