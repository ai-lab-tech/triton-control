"""Bind optional benchmark evidence to a managed task deployment and its repository."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from botocore.config import Config
from sqlmodel import Session

from app.core.canonical import digest, inventory_digest
from app.core.operation_secrets import decode
from app.db.entities import TritonInstanceEntity
from app.exceptions import AppError, ConflictError
from app.repositories import deployment_intents
from app.schemas.deployment import CreateDeploymentRequest
from app.schemas.perf_jobs import BenchmarkLoggingOption
from app.services import task_workspace_access as workspace
from app.services.candidates.artifacts import CaptureLimits, S3Source
from app.services.deployment.operation_provider import DeploymentProvider, resource_key, subset
from app.services.perf_analyzer import benchmark_tracking as tracking
from app.services.snapshots.runtime import RuntimeProvider
from app.services.storage.s3_client import build_s3_client


def repository(instance: TritonInstanceEntity) -> S3Source:
    if not instance.s3_bucket:
        raise ConflictError("The deployment has no bound model repository.")
    client = build_s3_client(
        instance, client_config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1})
    )
    return S3Source(client, instance.s3_bucket, instance.s3_prefix or "", require_versioning=True)


def inspect_revision(source: S3Source) -> tuple[str, list[list[str]]]:
    # Include overwritten and deleted versions to detect a change back to older bytes.
    pages = source.client.get_paginator("list_object_versions").paginate(Bucket=source.bucket, Prefix=source.prefix)
    entries = []
    versions = set()
    for index, page in enumerate(pages):
        if index >= 30:
            raise ConflictError("Candidate version history exceeds capture limits.")
        for kind in ("Versions", "DeleteMarkers"):
            for obj in page.get(kind, []):
                if obj["VersionId"] == "null":
                    raise ConflictError("Candidate repository has unversioned objects.")
                entries.append((kind, obj["Key"], obj["VersionId"], obj["IsLatest"]))
                path = obj["Key"].removeprefix(source.prefix).split("/", 2)
                if kind == "Versions" and obj["IsLatest"] and len(path) == 3 and path[1].isdigit() and path[2]:
                    versions.add((path[0], path[1]))
    if not entries:
        raise ConflictError("Candidate repository has no versioned files.")
    fingerprint = digest([source.client.meta.endpoint_url, source.bucket, source.prefix, sorted(entries)])
    return fingerprint, [list(pair) for pair in sorted(versions)]


def launch_binding(instance: TritonInstanceEntity, request: CreateDeploymentRequest) -> dict[str, Any] | None:
    lineage = request.lineage
    inventory = (
        lineage.candidate.output_inventory if lineage and lineage.candidate else request.candidate_inventory_digest
    )
    if not inventory:
        return None
    try:
        revision, versions = inspect_revision(repository(instance))
        return {
            "revision": revision,
            "versions": versions,
            "inventory": inventory,
            "derived_uri": lineage.origin.record_id if lineage else None,
            "lineage_digest": lineage.lineage_digest if lineage else None,
        }
    except Exception:
        # A missing version or inaccessible store only disables optional evidence.
        return None


def verify_workload(session: Session, instance: TritonInstanceEntity, binding: dict[str, Any]) -> dict[str, Any]:
    intent = deployment_intents.for_instance(session, instance.id or 0)
    if not intent or not instance.deployment_name or not instance.deployment_namespace:
        raise ConflictError("The managed deployment is unavailable.")
    manifests = decode(intent.bundle_enc)["manifests"]
    provider = DeploymentProvider()
    try:
        for kind, name in (("Deployment", instance.deployment_name), ("Service", instance.deployment_service_name)):
            expected = next(m for m in manifests if m["kind"] == kind and m["metadata"]["name"] == name)
            actual = provider.read(expected)
            uid = intent.resource_uids.get(resource_key(expected))
            if not uid or not actual or not subset(expected["spec"], actual["spec"]):
                raise ConflictError("The workload no longer matches its task deployment.")
            provider.verify(actual, intent.operation_id, uid)
        if actual is None:
            raise ConflictError("The serving Service is unavailable.")
        selector = actual["spec"]["selector"]
        pods = provider.call(
            f"/api/v1/namespaces/{instance.deployment_namespace}/pods",
            "GET",
            query=[("labelSelector", ",".join(f"{k}={v}" for k, v in selector.items()))],
        )
        selected = {p["metadata"]["uid"] for p in pods["items"] if not p["metadata"].get("deletionTimestamp")}
    finally:
        provider.close()
    observed = RuntimeProvider().observe(instance.deployment_namespace, instance.deployment_name)
    if (
        observed.deployment_uid != intent.resource_uids.get("Deployment/" + instance.deployment_name)
        or {image.pod_uid for image in observed.images} != selected
        or not observed.images
        or any(not image.observed for image in observed.images)
        or inspect_revision(repository(instance))[0] != binding["revision"]
    ):
        raise ConflictError("The served candidate or runtime has changed.")
    return {
        "fingerprint": digest(observed.model_dump(mode="json", exclude={"resource_version"})),
        "deployment_uid": observed.deployment_uid,
        "generation": observed.generation,
        "images": sorted({image.observed for image in observed.images if image.observed}),
    }


def resolve(
    session: Session, claims: dict[str, Any], instance: TritonInstanceEntity, model: str, version: str, *, submit: bool
) -> dict[str, Any]:
    if not instance.preparation_task_id:
        raise ConflictError("MLflow logging requires a serving launched from a task workspace.")
    task_id = UUID(instance.preparation_task_id)
    actor = workspace.authorize(session, claims, task_id)
    binding = instance.perf_candidate
    if not binding:
        raise ConflictError("The deployment has no verified candidate revision.")
    if [model, version] not in binding["versions"]:
        raise ConflictError("The selected model/version is absent from the task candidate.")
    inputs = workspace.load_inputs(session, task_id)
    workspace.authorize_inputs(session, inputs)
    if inputs.snapshot.kind != "mlflow":
        raise ConflictError("The task has no immutable Dev MLflow source model.")
    source = inputs.snapshot.origin
    lineage = tracking.resolve_model(session, binding, source.record_id, source.installation_id)
    if submit:
        runtime = verify_workload(session, instance, binding)
        with TemporaryDirectory() as temp:
            capture = repository(instance).capture(Path(temp), CaptureLimits())
        if inventory_digest(capture.inventory) != binding["inventory"] or not any(
            f.path.startswith(f"{model}/{version}/") for f in capture.inventory.files
        ):
            raise ConflictError("The served model/version does not match the converted model inventory.")
        lineage.update(runtime=runtime, actor_id=actor, creator=claims.get("email"), task_id=str(task_id), **binding)
    return lineage


def option(
    session: Session, claims: dict[str, Any], instance: TritonInstanceEntity, model: str, version: str
) -> BenchmarkLoggingOption | None:
    if not instance.preparation_task_id:
        return None
    try:
        lineage = resolve(session, claims, instance, model, version, submit=False)
    except AppError as exc:
        return BenchmarkLoggingOption(reason=exc.detail)
    except Exception:
        return BenchmarkLoggingOption(reason="Could not verify Dev MLflow lineage.")
    return BenchmarkLoggingOption(
        available=True,
        derived_model=lineage["model_id"],
        source_model=lineage["source"],
        source_url=lineage["source_url"],
    )
