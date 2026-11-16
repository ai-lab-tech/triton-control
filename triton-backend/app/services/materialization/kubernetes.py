"""Single immutable pod-template path for validation and future release consumers.

This module renders resources, not approval or production execution. The caller
must authorize a local storage binding and verify release evidence independently.
"""

from typing import Any

from app.core.canonical import canonical_bytes, inventory_digest
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, StorageBinding
from app.schemas.serving_metadata import Name, ServingRelease
from app.services.materialization.normalization import MaterializationError
from app.services.materialization.qualification import require_qualified


def manifests(
    release: ServingRelease,
    plan: MaterializationPlan,
    policy: MaterializerPolicy,
    binding: StorageBinding,
    *,
    name: str,
    namespace: str,
) -> list[dict[str, Any]]:
    from pydantic import TypeAdapter

    TypeAdapter(Name).validate_python(name)
    TypeAdapter(Name).validate_python(namespace)
    require_qualified(policy)
    repository = release.spec.repository
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
    config_name = name[:46].rstrip("-") + "-materialization"
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
        {"name": "model-repository", "emptyDir": {"sizeLimit": "32Gi"}},
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
    resources = release.spec.resources_per_replica
    requests: dict[str, str] = resources.requests.model_dump()
    limits: dict[str, str] = resources.limits.model_dump()
    if resources.gpu_count:
        requests["nvidia.com/gpu"] = limits["nvidia.com/gpu"] = str(resources.gpu_count)
    return [
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "immutable": True,
            "metadata": {"name": config_name, "namespace": namespace},
            "data": {"plan.json": data.decode()},
        },
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": namespace},
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
                            "requests": {"cpu": "100m", "memory": "128Mi"},
                            "limits": {"cpu": "1", "memory": "512Mi"},
                        },
                    }
                ],
                "containers": [
                    {
                        "name": "triton",
                        "image": release.spec.image,
                        "command": ["tritonserver"],
                        "args": ["--model-repository=/models", "--model-control-mode=none", "--strict-readiness=true"],
                        "securityContext": security,
                        "resources": {"requests": requests, "limits": limits},
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
                    }
                ],
                "volumes": volumes,
            },
        },
    ]
