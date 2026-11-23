"""Build a development startup template from the ready versions on one owned pod."""

import json
import os
import re
import shlex
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import httpx
from botocore.exceptions import BotoCoreError, ClientError

from app.core.canonical import digest, inventory_digest
from app.db.entities import TritonInstanceEntity
from app.exceptions import ConflictError
from app.schemas.experiment_snapshots import selected_model_path
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, StorageBinding
from app.schemas.pods import PodState
from app.schemas.serving_metadata import Repository, StartupModel
from app.services.candidates.artifacts import S3Source
from app.services.deployment.kubernetes import _aws_s3_source
from app.services.materialization.budgets import configured_limits
from app.services.materialization.kubernetes import serving_manifests
from app.services.materialization.qualification import require_qualified
from app.services.snapshots.runtime import actual_image
from app.services.storage.s3_client import build_s3_client

POLICY_ENV = "TRITON_MODEL_SCALE_POLICY"


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


class LoadedModelsSource(S3Source):
    def __init__(self, client: Any, storage: StorageBinding, models: tuple[StartupModel, ...], wrap: bool):
        super().__init__(client, storage.bucket, storage.prefix)
        self.models = {model.name: set(model.versions) for model in models}
        self.root = models[0].name + "/" if wrap else ""

    def includes(self, path: str) -> bool:
        return selected_model_path(self.root + path, self.models)


def render(
    instance: TritonInstanceEntity,
    raw: dict[str, Any],
    pod: PodState,
    models: tuple[StartupModel, ...],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    spec = raw["spec"]["template"]["spec"]
    runtime = next(c for c in spec["containers"] if c["name"] == "triton")
    status = next((c for c in pod.containers if c.name == "triton"), None)
    if not instance.s3_bucket or not instance.deployment_secret_name:
        raise ConflictError("Scaling requires a managed S3 repository and storage credentials")
    try:
        config = json.loads(os.environ[POLICY_ENV])
        policy = MaterializerPolicy.model_validate(config["policy"])
        require_qualified(policy)
        limits = configured_limits(config["limits"])
    except (KeyError, TypeError, ValueError) as error:
        raise ConflictError("Configure the qualified scaling materializer and capture limits on the backend") from error
    observed = status.image_id if status else None
    image = actual_image(observed) or actual_image(runtime.get("image"))
    if not image and observed and re.fullmatch(r"docker://sha256:[0-9a-f]{64}", observed):
        # Docker reports the config ID. Operators can bind it to the verified manifest once per image.
        image = actual_image(config.get("runtime_manifests", {}).get(observed))
    if not image:
        raise ConflictError("Pin the runtime image or configure a verified manifest for its observed image ID")
    script = runtime.get("args", [""])[0].strip()
    script = script.removeprefix("until [ -f /models/.s3-sync-ready ]; do sleep 1; done\n")
    if runtime.get("command") != ["/bin/bash", "-c"] or not script.startswith("exec tritonserver "):
        raise ConflictError("Automatic scaling requires dependencies baked into the serving image")
    args = shlex.split(script)
    source = next((arg.split("=", 1)[1] for arg in args if arg.startswith("--model-repository=")), "")
    if source.startswith("s3://"):
        uri, endpoint = _aws_s3_source(source)
    elif source == "/models":
        sync: dict[str, Any] = next(
            (c for c in [*spec.get("initContainers", []), *spec["containers"]] if c["name"] == "s3-model-sync"), {}
        )
        env = {e["name"]: e.get("value") for e in sync.get("env", [])}
        uri, endpoint = env.get("S3_SOURCE") or "", env.get("S3_ENDPOINT")
    else:
        raise ConflictError("The serving pod has no managed S3 repository")
    prefix = (instance.s3_prefix or "").strip("/")
    expected_uri = f"s3://{instance.s3_bucket}/{prefix}".rstrip("/")
    if (uri or "").rstrip("/") != expected_uri or endpoint != (instance.s3_endpoint or None):
        raise ConflictError("The configured S3 repository differs from the deployed source")
    try:
        storage = StorageBinding(
            bucket=instance.s3_bucket,
            prefix=prefix,
            endpoint=endpoint,
            region=instance.s3_region or "us-east-1",
            secret_name=instance.deployment_secret_name,
            allow_http=(endpoint or "").startswith("http://"),
            ca_secret_name=config.get("ca_secret_name"),
        )
    except (KeyError, ValueError, TypeError) as error:
        raise ConflictError("Configure the qualified scaling materializer and capture limits on the backend") from error

    try:
        with closing(build_s3_client(instance)) as client, TemporaryDirectory() as directory:
            # The sync worker wraps a single-model prefix when config.pbtxt is at its root.
            root_key = (prefix + "/" if prefix else "") + "config.pbtxt"
            listing = client.list_objects_v2(Bucket=storage.bucket, Prefix=root_key, MaxKeys=1)
            wrap = any(obj["Key"] == root_key for obj in listing.get("Contents", []))
            if wrap and len(models) != 1:
                raise ConflictError("A single-model S3 root cannot provide every loaded model")
            captured = LoadedModelsSource(client, storage, models, wrap).capture(Path(directory), limits)
    except (BotoCoreError, ClientError) as error:
        raise ConflictError("Could not read the configured S3 repository for scaling") from error
    objects = captured.receipt["objects"]
    if any(obj["version_id"] in (None, "", "null") for obj in objects.values()):
        raise ConflictError("Enable S3 versioning and publish versioned objects before scaling")
    inventory = captured.inventory
    prepared = (
        inventory.model_copy(
            update={
                "files": tuple(
                    entry.model_copy(update={"path": models[0].name + "/" + entry.path}) for entry in inventory.files
                )
            }
        )
        if wrap
        else inventory
    )
    versions = {
        (parts[0], parts[1])
        for file in prepared.files
        if len(parts := file.path.split("/")) >= 3 and parts[1].isdigit()
    }
    if versions != {(model.name, version) for model in models for version in model.versions}:
        raise ConflictError("S3 does not contain every ready model version")
    try:
        plan = MaterializationPlan.model_validate(
            {
                "materializer_image": policy.image,
                "source_inventory": inventory,
                "prepared_inventory": prepared,
                "objects": [{"path": path, "version_id": obj["version_id"]} for path, obj in objects.items()],
                "normalization": {"version": "triton-layout/v1", "wrap_model": models[0].name} if wrap else {},
            }
        )
        repository = Repository.model_validate(
            {
                "uri": expected_uri,
                "inventoryDigest": inventory_digest(inventory),
                "preparedInventoryDigest": inventory_digest(prepared),
                "materializerImage": policy.image,
            }
        )
        identity = digest(
            {
                "plan": plan.model_dump(mode="json"),
                "image": image,
                "models": [model.model_dump() for model in models],
                "deployment_uid": raw["metadata"]["uid"],
                "template": raw["spec"]["template"],
            }
        )
        manifest, workload = serving_manifests(
            plan,
            policy,
            storage,
            name="startup-" + identity[7:29],
            namespace=raw["metadata"]["namespace"],
            limits=limits,
            identity_digest=identity,
            image=image,
            repository=repository,
            models=models,
            resources=runtime.get("resources", {}),
        )
        workload["spec"]["containers"][0]["args"].extend(
            arg for arg in args if arg.startswith(("--allow-metrics=", "--allow-cpu-metrics=", "--allow-gpu-metrics="))
        )
    except ValueError as error:
        raise ConflictError("The loaded models could not be represented as an immutable startup snapshot") from error
    return identity, manifest, workload
