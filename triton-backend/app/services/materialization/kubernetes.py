"""Single immutable pod-template path for validation and future release consumers.

The release entry point verifies admission before rendering. Validation and
development callers also use the lower-level pod-template renderer.
"""

import os
from dataclasses import asdict, dataclass
from typing import Any, Literal

from app.core import installation_policy
from app.core.canonical import canonical_bytes, digest, inventory_digest
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, StorageBinding
from app.schemas.serving_metadata import Name, Repository, Resources, ServingRelease, StartupModel
from app.schemas.validation import HardwareRuntime
from app.services.candidates.artifacts import CaptureLimits
from app.services.contract_io import MetadataError
from app.services.gate_evidence import POLICY_FILE, admit_release
from app.services.materialization.budgets import check_plan, storage_bytes
from app.services.materialization.normalization import MaterializationError
from app.services.materialization.qualification import require_qualified
from app.services.materialization.readiness import probe_timeout_seconds
from app.services.materialization.runtime import receipt_for


@dataclass(frozen=True)
class ReleaseApproval:
    archive: bytes
    target: HardwareRuntime
    binding: str


def manifests(
    release: ServingRelease,
    plan: MaterializationPlan,
    policy: MaterializerPolicy,
    binding: StorageBinding,
    *,
    name: str,
    namespace: str,
    limits: CaptureLimits,
    approval: ReleaseApproval | None = None,
) -> list[dict[str, Any]]:
    if approval is not None:
        if policy.platform != f"{approval.target.operating_system}/{approval.target.architecture}":
            raise MetadataError("release-approval-platform-mismatch")
        admit_release(release, approval.archive, target=approval.target, approved_binding=approval.binding)
    elif release.spec.security is not None or POLICY_FILE in os.environ or installation_policy.mode() == "production":
        raise MetadataError("release-approval-required")
    return serving_manifests(
        plan, policy, binding, name=name, namespace=namespace, limits=limits,
        identity_digest=digest(release.model_dump(mode="json", by_alias=True)), image=release.spec.image,
        repository=release.spec.repository, models=release.spec.models, resources=release.spec.resources_per_replica,
    )


def serving_manifests(
    plan: MaterializationPlan, policy: MaterializerPolicy, binding: StorageBinding, *,
    name: str, namespace: str, limits: CaptureLimits, identity_digest: str, image: str,
    repository: Repository, models: tuple[StartupModel, ...], resources: Resources,
    subject: Literal["release", "validation-manifest"] = "release",
) -> list[dict[str, Any]]:
    from pydantic import TypeAdapter

    TypeAdapter(Name).validate_python(name)
    TypeAdapter(Name).validate_python(namespace)
    require_qualified(policy)
    check_plan(plan, limits)
    if (
        plan.materializer_image != policy.image
        or repository.materializer_image != policy.image
        or repository.inventory_digest != inventory_digest(plan.source_inventory)
        or repository.prepared_inventory_digest != inventory_digest(plan.prepared_inventory)
    ):
        raise MaterializationError("Release, plan and qualified materializer subjects differ")
    if repository.uri.rstrip("/") != f"s3://{binding.bucket}/{binding.prefix}":
        raise MaterializationError("Repository does not match the authorized storage binding")
    data = canonical_bytes(plan.model_dump(mode="json"))
    if len(data) > 512 * 1024:
        raise MaterializationError("Materialization plan exceeds Kubernetes document limit")
    budget_data = canonical_bytes(asdict(limits))
    readiness = {
        subject: identity_digest,
        "receipt": receipt_for(plan).model_dump(mode="json"),
        "models": [model.model_dump(mode="json") for model in models],
    }
    readiness_data = canonical_bytes(readiness)
    if len(data) + len(readiness_data) + len(budget_data) > 900 * 1024:
        raise MaterializationError("Release probe expectations exceed Kubernetes document limit")
    identity = digest(
        {"name": name, subject: identity_digest, "plan": readiness["receipt"], "limits": asdict(limits)}
    )
    config_name = name[:30].rstrip("-") + "-materialization-" + identity.removeprefix("sha256:")[:16]
    repository_storage = str(storage_bytes(plan))
    env: list[dict[str, Any]] = [
        {
            "name": "AWS_ACCESS_KEY_ID",
            "valueFrom": {"secretKeyRef": {"name": binding.secret_name, "key": "AWS_ACCESS_KEY_ID"}},
        },
        {
            "name": "AWS_SECRET_ACCESS_KEY",
            "valueFrom": {"secretKeyRef": {"name": binding.secret_name, "key": "AWS_SECRET_ACCESS_KEY"}},
        },
        {"name": "AWS_DEFAULT_REGION", "value": binding.region},
        {"name": "AWS_EC2_METADATA_DISABLED", "value": "true"},
    ]
    args = [
        "run",
        "--plan",
        "/plan/plan.json",
        "--limits",
        "/plan/limits.json",
        "--output",
        "/materialized",
        "--expected-image",
        policy.image,
        "--bucket",
        binding.bucket,
        "--prefix",
        binding.prefix,
    ]
    if binding.endpoint:
        args.extend(["--endpoint", binding.endpoint])
    if binding.allow_http:
        args.append("--allow-http")
    volumes: list[dict[str, Any]] = [
        {"name": "model-repository", "emptyDir": {"sizeLimit": repository_storage}},
        {"name": "materialization-plan", "configMap": {"name": config_name}},
        {"name": "runtime-tmp", "emptyDir": {"sizeLimit": "1Gi"}},
    ]
    init_mounts: list[dict[str, Any]] = [
        {"name": "model-repository", "mountPath": "/materialized"},
        {"name": "materialization-plan", "mountPath": "/plan", "readOnly": True},
    ]
    if binding.ca_secret_name:
        volumes.append({"name": "storage-ca", "secret": {"secretName": binding.ca_secret_name}})
        init_mounts.append({"name": "storage-ca", "mountPath": "/storage-ca", "readOnly": True})
        args.extend(["--ca-bundle", "/storage-ca/ca.crt"])
    security = {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}}
    requests: dict[str, str] = resources.requests.model_dump()
    runtime_limits: dict[str, str] = resources.limits.model_dump()
    if resources.gpu_count:
        requests["nvidia.com/gpu"] = runtime_limits["nvidia.com/gpu"] = str(resources.gpu_count)
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "immutable": True,
            "metadata": {"name": config_name, "namespace": namespace},
            "data": {
                "plan.json": data.decode(),
                "limits.json": budget_data.decode(),
                "readiness.json": readiness_data.decode(),
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "annotations": {
                    f"triton-control/{subject}-digest": identity_digest,
                    "triton-control/prepared-inventory-digest": repository.prepared_inventory_digest,
                },
            },
            "spec": {
                "automountServiceAccountToken": False,
                "nodeSelector": {"kubernetes.io/os": "linux", "kubernetes.io/arch": policy.platform.split("/")[1]},
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "fsGroup": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "initContainers": [
                    {
                        "name": "materialize-models",
                        "image": policy.image,
                        "args": args,
                        "securityContext": security,
                        "env": env,
                        "volumeMounts": init_mounts,
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "128Mi", "ephemeral-storage": repository_storage},
                            "limits": {"cpu": "1", "memory": "512Mi"},
                        },
                    }
                ],
                "containers": [
                    {
                        "name": "triton",
                        "image": image,
                        "command": ["tritonserver"],
                        "startupProbe": {
                            "httpGet": {"path": "/v2/health/live", "port": 8000},
                            "periodSeconds": 5,
                            "timeoutSeconds": 2,
                            "failureThreshold": 360,
                        },
                        "readinessProbe": {
                            "httpGet": {"path": "/v2/health/ready", "port": 8000},
                            "periodSeconds": 5,
                            "timeoutSeconds": 2,
                            "failureThreshold": 1,
                        },
                        "livenessProbe": {
                            "httpGet": {"path": "/v2/health/live", "port": 8000},
                            "periodSeconds": 10,
                            "timeoutSeconds": 2,
                            "failureThreshold": 6,
                        },
                        "args": ["--model-repository=/models", "--model-control-mode=none", "--strict-readiness=true"],
                        "securityContext": security,
                        "resources": {"requests": requests, "limits": runtime_limits},
                        "volumeMounts": [
                            {
                                "name": "model-repository",
                                "mountPath": "/models",
                                "subPath": "ready/models",
                                "readOnly": True,
                            },
                            {
                                "name": "model-repository",
                                "mountPath": "/var/run/triton-materialization/receipt.json",
                                "subPath": "ready/receipt.json",
                                "readOnly": True,
                            },
                            {"name": "runtime-tmp", "mountPath": "/tmp"},  # nosec B108
                        ],
                    },
                    {
                        "name": "release-readiness",
                        "image": policy.image,
                        "command": ["python", "-c", "import signal; signal.pause()"],
                        "securityContext": security,
                        "resources": {
                            "requests": {"cpu": "10m", "memory": "32Mi"},
                            "limits": {"cpu": "250m", "memory": "64Mi"},
                        },
                        "volumeMounts": [
                            {"name": "materialization-plan", "mountPath": "/plan", "readOnly": True},
                            {
                                "name": "model-repository",
                                "mountPath": "/var/run/triton-materialization/receipt.json",
                                "subPath": "ready/receipt.json",
                                "readOnly": True,
                            },
                        ],
                        "readinessProbe": {
                            "exec": {"command": ["python", "-m", "app.services.materialization.readiness"]},
                            "periodSeconds": 5,
                            "timeoutSeconds": probe_timeout_seconds(sum(len(m.versions) for m in models)),
                            "failureThreshold": 1,
                        },
                    },
                ],
                "volumes": volumes,
            },
        },
    ]
