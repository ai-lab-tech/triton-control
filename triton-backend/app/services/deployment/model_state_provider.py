"""UID-fenced reads and updates for the development Triton Deployment."""

import json
from copy import deepcopy
from typing import Any

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.core.canonical import digest
from app.exceptions import BadGatewayError, ConflictError
from app.schemas.materialization import MaterializationPlan, MaterializerPolicy, StorageBinding
from app.schemas.model_state import DevelopmentStartupRelease
from app.schemas.serving_metadata import ServingRelease, StartupModel
from app.schemas.validation import HardwareRuntime
from app.services.deployment.pod_provider import RELEASE_KEY, TIMEOUT, PodProvider, controlled_by
from app.services.deployment.replica_startup import serving_template
from app.services.materialization.budgets import configured_limits
from app.services.materialization.kubernetes import ReleaseApproval, manifests, serving_manifests
from app.services.materialization.normalization import MaterializationError

ROLLBACK_KEY = "triton-control/interactive-template"


SCALE_TARGET = "triton-control/scale-target"


class ModelStateProvider(PodProvider):
    def has_autoscaler(self, namespace: str, name: str) -> bool:
        api = client.AutoscalingV2Api(self.api)
        return any(
            (target := item.get("spec", {}).get("scaleTargetRef", {})).get("kind") == "Deployment"
            and target.get("name") == name
            for item in self.items(api.list_namespaced_horizontal_pod_autoscaler, namespace)
        )

    def patch(
        self, deployment: dict[str, Any], path: str, value: Any, *, replace_single_pod: bool = False,
        rolling_target: int | None = None, clear_target: bool = False,
    ) -> None:
        meta = deployment["metadata"]
        changes = [
            {"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
            {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
            {"op": "add", "path": path, "value": value},
        ]
        if rolling_target is not None or clear_target:
            annotations = dict(meta.get("annotations") or {})
            if rolling_target is None:
                annotations.pop(SCALE_TARGET, None)
            else:
                annotations[SCALE_TARGET] = str(rolling_target)
            changes.append({"op": "add", "path": "/metadata/annotations", "value": annotations})
        if rolling_target is not None:
            changes.append({"op": "add", "path": "/spec/strategy", "value": {
                "type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
            }})
        if replace_single_pod:
            # A one-GPU installation may have no capacity for a rolling surge.
            changes.append({"op": "add", "path": "/spec/strategy", "value": {"type": "Recreate"}})
        self.apps.patch_namespaced_deployment(meta["name"], meta["namespace"], changes, _request_timeout=TIMEOUT)

    def immutable_config(self, namespace: str, name: str) -> dict[str, Any]:
        try:
            config = self.data(self.core.read_namespaced_config_map(name, namespace, _request_timeout=TIMEOUT))
        except ApiException as error:
            if error.status == 404:
                raise ConflictError(f"Required immutable ConfigMap {name} is unavailable") from error
            raise
        if config.get("immutable") is not True:
            raise ConflictError(f"ConfigMap {name} must be immutable")
        return config

    def ensure_config(self, deployment: dict[str, Any], config: dict[str, Any]) -> None:
        meta = deployment["metadata"]
        config["metadata"]["ownerReferences"] = [{
            "apiVersion": "apps/v1", "kind": "Deployment", "name": meta["name"],
            "uid": meta["uid"], "controller": True,
        }]
        try:
            self.core.create_namespaced_config_map(meta["namespace"], config, _request_timeout=TIMEOUT)
        except ApiException as error:
            if error.status != 409:
                raise
            existing = self.immutable_config(meta["namespace"], config["metadata"]["name"])
            if not controlled_by(existing, "Deployment", meta["uid"]) or existing.get("data") != config["data"]:
                raise ConflictError("Existing startup ConfigMap is not owned by this deployment") from error

    def declare(self, deployment: dict[str, Any], release_digest: str) -> None:
        meta = deployment["metadata"]
        namespace, name = meta["namespace"], meta["name"]
        binding_name = f"{name}.startup.{release_digest.removeprefix('sha256:')[:32]}"
        source = self.immutable_config(namespace, binding_name)
        try:
            binding = json.loads(source["data"]["startup.json"])
            value = binding["release"]
            release: ServingRelease | DevelopmentStartupRelease
            if isinstance(value, dict) and value.get("kind") == "ServingRelease":
                release = ServingRelease.model_validate(value)
            else:
                release = DevelopmentStartupRelease.model_validate(value)
            plan = MaterializationPlan.model_validate(binding["plan"])
            policy = MaterializerPolicy.model_validate(binding["policy"])
            storage = StorageBinding.model_validate(binding["storage"])
            if release_digest != digest(release.model_dump(mode="json", by_alias=True)):
                raise MaterializationError("Prepared release digest differs from the request")
            expected = {model.name: set(model.versions) for model in release.spec.models}
            if len(expected) != len(release.spec.models):
                raise MaterializationError("Prepared startup release has duplicate model names")
            entries: dict[str, set[str]] = {}
            for entry in plan.prepared_inventory.files:
                parts = entry.path.split("/")
                if len(parts) < 2 or parts[0] not in expected:
                    raise MaterializationError("Prepared repository contains undeclared model content")
                versions = entries.setdefault(parts[0], set())
                if parts[1].isascii() and parts[1].isdigit():
                    if len(parts) == 2:
                        raise MaterializationError("Prepared repository has a file at a model version path")
                    versions.add(parts[1])
            if entries != expected:
                raise MaterializationError("Prepared model versions differ from the declared startup set")
            render_id = digest({"uid": meta["uid"], "release": release_digest, "binding": binding})
            pod_name = f"startup-{render_id.removeprefix('sha256:')[:22]}"
            limits = configured_limits(binding["limits"])
            if isinstance(release, ServingRelease):
                evidence = binding.get("approval")
                approval = None
                if evidence is not None:
                    archive = evidence["archive"]
                    if not isinstance(archive, str):
                        raise ValueError("Release evidence archive must be JSON text")
                    approval = ReleaseApproval(
                        archive.encode("utf-8"), HardwareRuntime.model_validate(evidence["target"]), evidence["binding"]
                    )
                config, pod = manifests(
                    release, plan, policy, storage,
                    name=pod_name, namespace=namespace, limits=limits, approval=approval,
                )
            else:
                config, pod = serving_manifests(
                    plan, policy, storage, name=pod_name, namespace=namespace, limits=limits,
                    identity_digest=release_digest, image=release.spec.image,
                    repository=release.spec.repository, models=release.spec.models,
                    resources=release.spec.resources_per_replica,
                )
        except (KeyError, TypeError, ValueError) as error:
            raise ConflictError("Prepared startup release or materialization binding is invalid") from error

        old = deployment["spec"]["template"]
        old_spec = old["spec"]
        rollback_name = self.save_interactive_template(deployment)
        self.ensure_config(deployment, config)
        template = {"metadata": deepcopy(old["metadata"]), "spec": pod["spec"]}
        template["metadata"]["annotations"] = {
            **(old["metadata"].get("annotations") or {}),
            RELEASE_KEY: release_digest,
            ROLLBACK_KEY: rollback_name,
        }
        for key in ("imagePullSecrets", "runtimeClassName", "tolerations", "affinity", "priorityClassName"):
            if key in old_spec:
                template["spec"][key] = old_spec[key]
        template["spec"]["nodeSelector"] = {
            **old_spec.get("nodeSelector", {}), **template["spec"]["nodeSelector"],
        }
        template["spec"]["containers"][0]["ports"] = [
            {"name": name, "containerPort": port}
            for name, port in (("http", 8000), ("grpc", 8001), ("metrics", 8002))
        ]
        self.patch(deployment, "/spec/template", template, replace_single_pod=True)

    def save_interactive_template(self, deployment: dict[str, Any]) -> str:
        meta = deployment["metadata"]
        namespace = meta["namespace"]
        old = deployment["spec"]["template"]
        rollback_name = "tc-interactive-" + digest(old).removeprefix("sha256:")[:24] + "-" + str(meta["uid"])[:8]
        self.ensure_config(deployment, {
            "apiVersion": "v1", "kind": "ConfigMap", "immutable": True,
            "metadata": {"name": rollback_name, "namespace": namespace},
            "data": {"template.json": json.dumps(old, separators=(",", ":"))},
        })
        return rollback_name

    def scale_serving(self, deployment: dict[str, Any], models: tuple[StartupModel, ...], replicas: int) -> None:
        template = serving_template(deployment["spec"]["template"], models)
        annotations = dict(template["metadata"].get("annotations") or {})
        annotations[RELEASE_KEY] = digest(template)
        annotations[ROLLBACK_KEY] = self.save_interactive_template(deployment)
        template["metadata"]["annotations"] = annotations
        self.patch(deployment, "/spec/template", template, rolling_target=replicas)

    def restore(self, deployment: dict[str, Any], backup: str) -> None:
        meta = deployment["metadata"]
        config = self.immutable_config(meta["namespace"], backup)
        if not controlled_by(config, "Deployment", meta["uid"]):
            raise ConflictError("Interactive template does not belong to this deployment")
        try:
            template = json.loads(config["data"]["template.json"])
        except (KeyError, ValueError) as error:
            raise BadGatewayError("Kubernetes returned an invalid interactive template") from error
        if SCALE_TARGET in (meta.get("annotations") or {}):
            self.patch(deployment, "/spec/template", template, clear_target=True)
        else:
            self.patch(deployment, "/spec/template", template, replace_single_pod=True)
