"""Synthetic bytes and identities for materializer tests; not deployable models."""

import hashlib
import json
from pathlib import Path

from app.core.canonical import digest, inventory_digest
from app.schemas.lineage import FileEntry, InventoryV1
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, Normalization, ObjectVersion
from app.schemas.serving_metadata import ServingRelease
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.qualification import REQUIRED_CHECKS

LIMITS = CaptureLimits(max_bytes=40 * 1024**3, max_file_bytes=40 * 1024**3, deadline_seconds=3600)

FILES = {
    "config.pbtxt": b'name: "iris"\nbackend: "vllm"\n',
    "1/model.json": b'{"model":"weights"}\n',
    "1/weights/model.bin": b"synthetic model fixture",
}
IMAGE = "fixture.invalid/materializer@" + digest({"fixture": "materializer-image"})


def inventory(files):
    return InventoryV1(
        files=tuple(
            FileEntry(path=path, size=len(content), digest="sha256:" + hashlib.sha256(content).hexdigest())
            for path, content in sorted(files.items())
        )
    )


def plan(image=IMAGE):
    normalization = Normalization(
        version="triton-layout/v1",
        wrap_model="iris",
        rewrites=(
            {
                "file": "1/model.json",
                "relative": "weights",
                "target": "iris/1/weights",
            },
        ),
    )
    prepared = {"iris/" + path: content for path, content in FILES.items()}
    prepared["iris/1/model.json"] = b'{"model":"/models/iris/1/weights"}\n'
    return MaterializationPlan(
        materializer_image=image,
        source_inventory=inventory(FILES),
        prepared_inventory=inventory(prepared),
        normalization=normalization,
        objects=tuple(ObjectVersion(path=path, version_id="v1") for path in FILES),
    )


def policy(image=IMAGE, platform="linux/amd64"):
    # This synthetic record is solely a renderer fixture, not provisioning evidence.
    return MaterializerPolicy(
        image=image,
        platform=platform,
        qualification={
            "image": image,
            "platform": platform,
            "fixture_revision": "materializer-qualification/v3",
            "checks": dict.fromkeys(REQUIRED_CHECKS, True),
            "evidence": {"uri": "fixture-only.json", "digest": digest({"synthetic": True})},
        },
    )


def serving_release() -> ServingRelease:
    path = Path(__file__).resolve().parents[2] / "contracts/serving/v1alpha1/fixtures/release-manual.json"
    payload = json.loads(path.read_text())
    spec = payload["spec"]
    repository = spec["repository"]
    repository.update(
        uri="s3://models/repository",
        inventoryDigest=inventory_digest(plan().source_inventory),
        preparedInventoryDigest=inventory_digest(plan().prepared_inventory),
        materializerImage=IMAGE,
    )
    for name in ("validation", "security"):
        if spec.get(name):
            spec[name]["subjects"].update(
                inventoryDigest=repository["inventoryDigest"],
                preparedInventoryDigest=repository["preparedInventoryDigest"],
                materializerImage=IMAGE.split("@", 1)[1],
            )
    return ServingRelease.model_validate(payload)

class DirectoryFixture:
    def download(self, plan, destination, limits):
        for path, data in FILES.items():
            target = Path(destination) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
