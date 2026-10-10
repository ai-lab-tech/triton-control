"""Kubernetes operations and manifest rendering for singleton MLflow installs."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from pathlib import Path
from typing import Any, cast

import httpx
import yaml  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError, BadRequestError
from app.schemas import InstallMlflowRequest
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster
from app.services.mlflow import config, tracking

_TEMPLATE = Path(__file__).with_name("mlflow_deployment.yaml")
_POD_WAIT_ATTEMPTS = 120
_POD_WAIT_INTERVAL_SECONDS = 5
_POD_TERMINAL_PHASES = {"Failed", "Unknown"}
_POD_TERMINAL_REASONS = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "OOMKilled",
    "Error",
    "CreateContainerConfigError",
    "InvalidImageName",
}


def apply_installation_resources(
    request: InstallMlflowRequest,
    *,
    namespace: str,
    deployment_name: str,
    service_name: str,
) -> list[str]:
    """Apply namespace-scoped resources needed for an MLflow server."""
    from kubernetes import utils  # type: ignore[import-untyped]
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
    from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]
    from kubernetes.utils.create_from_yaml import FailToCreateError  # type: ignore[import-untyped]

    try:
        api = _client()
        _ensure_namespace(api, namespace)
        applied = []
        token = _gateway_token(api, namespace, deployment_name)
        for manifest in _manifests(request, namespace, deployment_name, service_name, gateway_token=token):
            utils.create_from_dict(api, data=manifest, namespace=namespace, verbose=False, apply=True)
            meta = manifest.get("metadata") or {}
            applied.append(f"{manifest.get('kind', 'Resource')}/{meta.get('name', 'unknown')}")
        _wait_for_ready_pod(api, namespace, deployment_name)
        return applied
    except ConfigException as exc:
        raise BadRequestError("Kubernetes configuration could not be loaded") from exc
    except ApiException as exc:
        raise BadGatewayError(_api_error(exc)) from exc
    except FailToCreateError as exc:
        errs = getattr(exc, "api_exceptions", []) or []
        message = "; ".join(_api_error(error) for error in errs) or "Failed to apply Kubernetes resources"
        raise BadGatewayError(message) from exc
    except (BadGatewayError, BadRequestError):
        raise
    except Exception as exc:
        raise BadGatewayError(f"MLflow installation failed: {exc}") from exc


def delete_namespace(namespace: str) -> str:
    """Request deletion of the namespace that owns the MLflow workload."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException
    from kubernetes.config.config_exception import ConfigException

    try:
        client.CoreV1Api(_client()).delete_namespace(name=namespace)
        return f"Namespace '{namespace}' deletion requested."
    except ConfigException:
        raise
    except ApiException as exc:
        if exc.status == 404:
            return f"Namespace '{namespace}' was already deleted."
        raise BadGatewayError(_api_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to delete MLflow namespace '{namespace}': {exc}") from exc


def tracking_upgrade_required(namespace: str, deployment_name: str) -> bool:
    """Detect legacy deployments and interrupted gateway migrations."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    api = _client()
    deployment = client.AppsV1Api(api).read_namespaced_deployment(deployment_name, namespace)
    containers = deployment.spec.template.spec.containers
    if not any(container.name == "tracking-gateway" for container in containers):
        return True
    try:
        secret = client.CoreV1Api(api).read_namespaced_secret(tracking.gateway_secret_name(deployment_name), namespace)
    except ApiException as exc:
        if exc.status == 404:
            return True
        raise
    return not (secret.data or {}).get("gateway-token")


def upgrade_installation_resources(namespace: str, deployment_name: str, service_name: str) -> list[str]:
    """Retain private-registry credentials when adding the tracking gateway."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    dockerconfigjson = None
    try:
        secret = client.CoreV1Api(_client()).read_namespaced_secret(
            _image_pull_secret_name(deployment_name), namespace,
        )
        dockerconfigjson = base64.b64decode((secret.data or {}).get(".dockerconfigjson", "")).decode() or None
    except ApiException as exc:
        if exc.status != 404:
            raise
    applied = apply_installation_resources(
        InstallMlflowRequest(installation_name=deployment_name, dockerconfigjson=dockerconfigjson),
        namespace=namespace, deployment_name=deployment_name, service_name=service_name,
    )
    _wait_for_current_deployment(_client(), namespace, deployment_name)
    _wait_for_gateway(namespace, deployment_name, service_name)
    return applied


def _wait_for_gateway(namespace: str, deployment_name: str, service_name: str) -> None:
    token = _gateway_token(_client(), namespace, deployment_name)
    with httpx.Client(timeout=5, trust_env=False) as http:
        for _ in range(_POD_WAIT_ATTEMPTS):
            try:
                response = http.get(
                    f"{service_url(namespace, service_name)}/health",
                    headers={"X-Triton-Mlflow-Gateway": token},
                )
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(_POD_WAIT_INTERVAL_SECONDS)
    raise BadGatewayError("MLflow tracking gateway authentication did not become ready")


def _wait_for_current_deployment(api: Any, namespace: str, deployment_name: str) -> None:
    from kubernetes import client

    apps = client.AppsV1Api(api)
    for _ in range(_POD_WAIT_ATTEMPTS):
        deployment = apps.read_namespaced_deployment(deployment_name, namespace)
        desired = deployment.spec.replicas or 1
        status = deployment.status
        if (
            (status.observed_generation or 0) >= deployment.metadata.generation
            and status.updated_replicas == desired
            and status.ready_replicas == desired
            and status.replicas == desired
        ):
            return
        time.sleep(_POD_WAIT_INTERVAL_SECONDS)
    raise BadGatewayError("MLflow tracking gateway rollout did not complete")


def delete_installation_resources(namespace: str, deployment_name: str, service_name: str) -> str:
    """Delete MLflow resources in-place without deleting namespace."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    apps = client.AppsV1Api(_client())
    core = client.CoreV1Api(_client())
    deleted: list[str] = []

    def _delete(callable_fn: Any, kind: str, name: str) -> None:
        if not (name or "").strip():
            return
        try:
            callable_fn(name=name, namespace=namespace)
            deleted.append(f"{kind}/{name}")
        except ApiException as exc:
            if exc.status != 404:
                raise BadGatewayError(_api_error(exc)) from exc

    _delete(apps.delete_namespaced_deployment, "Deployment", deployment_name)
    _delete(core.delete_namespaced_service, "Service", service_name)
    _delete(core.delete_namespaced_persistent_volume_claim, "PersistentVolumeClaim", _data_pvc_name(deployment_name))
    _delete(core.delete_namespaced_secret, "Secret", _image_pull_secret_name(deployment_name))
    _delete(core.delete_namespaced_secret, "Secret", tracking.gateway_secret_name(deployment_name))
    return ", ".join(deleted) if deleted else "No MLflow resources found to delete."


def read_installation_readiness(namespace: str, deployment_name: str) -> tuple[bool, str]:
    """Return (ready, message) for MLflow pod in a namespace."""
    from kubernetes.config.config_exception import ConfigException

    try:
        api = _client()
        pod_name = _ready_pod_name(api, namespace, deployment_name)
        if pod_name:
            return True, f"MLflow pod '{pod_name}' is Ready."
        reason = _pod_error_reason(api, namespace, deployment_name)
        if reason:
            return False, f"MLflow pod not ready: {reason}"
        return False, "MLflow installation exists but pod is not Ready yet."
    except ConfigException:
        return False, "Kubernetes configuration could not be loaded"
    except Exception as exc:
        return False, f"Failed to read MLflow readiness: {exc}"


def service_url(namespace: str, service_name: str) -> str:
    return f"http://{service_name}.{namespace}.svc.cluster.local:5000"


def _allowed_hosts(namespace: str, service_name: str) -> str:
    hosts = [
        service_name,
        f"{service_name}:5000",
        f"{service_name}.{namespace}",
        f"{service_name}.{namespace}:5000",
        f"{service_name}.{namespace}.svc",
        f"{service_name}.{namespace}.svc:5000",
        f"{service_name}.{namespace}.svc.cluster.local",
        f"{service_name}.{namespace}.svc.cluster.local:5000",
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
    ]
    return ",".join(hosts)


def _manifests(
    request: InstallMlflowRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
    *,
    gateway_token: str | None = None,
) -> list[dict[str, Any]]:
    def q(value: str) -> str:
        return cast(str, yaml.safe_dump(value, default_style='"').strip())

    rendered = _TEMPLATE.read_text(encoding="utf-8").format(
        image_pull_secret_manifest=_image_pull_secret_manifest(
            request.dockerconfigjson,
            namespace,
            deployment_name,
            q,
        ),
        image_pull_secret_reference=_image_pull_secret_reference(
            request.dockerconfigjson,
            deployment_name,
        ),
        namespace=q(namespace),
        deployment_name=q(deployment_name),
        service_name=q(service_name),
        data_pvc_name=q(_data_pvc_name(deployment_name)),
        image=q(config.server_image()),
        allowed_hosts=q(_allowed_hosts(namespace, service_name)),
        gateway_secret_name=q(tracking.gateway_secret_name(deployment_name)),
        gateway_image=q(os.getenv("MLFLOW_GATEWAY_IMAGE", "nginx:1.28-alpine")),
    )
    manifests = [manifest for manifest in yaml.safe_load_all(rendered) if manifest]
    gateway = tracking.gateway_manifest(namespace, deployment_name, gateway_token or secrets.token_urlsafe(32))
    checksum = hashlib.sha256(gateway["stringData"]["nginx.conf"].encode()).hexdigest()
    for manifest in manifests:
        if manifest["kind"] == "Deployment":
            manifest["spec"]["template"]["metadata"].setdefault("annotations", {})[
                "triton-control.ai/mlflow-gateway-checksum"
            ] = checksum
    manifests.append(gateway)
    # The gateway configuration must exist before its pod is scheduled.
    return sorted(manifests, key=lambda manifest: manifest["kind"] != "Secret")


def _gateway_token(api: Any, namespace: str, deployment_name: str) -> str:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    try:
        secret = client.CoreV1Api(api).read_namespaced_secret(tracking.gateway_secret_name(deployment_name), namespace)
    except ApiException as exc:
        if exc.status == 404:
            return secrets.token_urlsafe(32)
        raise
    token = base64.b64decode((secret.data or {}).get("gateway-token", "")).decode()
    if not token:
        return secrets.token_urlsafe(32)
    return token


def _client() -> Any:
    return api_client()


def _ensure_namespace(api: Any, namespace: str) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    if is_running_in_cluster() and in_cluster_namespace() == (namespace or "").strip():
        return

    v1 = client.CoreV1Api(api)
    try:
        v1.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    except ApiException as exc:
        if exc.status != 409:
            raise


def _wait_for_ready_pod(api: Any, namespace: str, deployment_name: str) -> None:
    for _ in range(_POD_WAIT_ATTEMPTS):
        if _ready_pod_name(api, namespace, deployment_name):
            return
        error = _pod_error_reason(api, namespace, deployment_name)
        if error:
            raise BadGatewayError(
                f"MLflow pod in namespace '{namespace}' failed to start: {error}"
            )
        time.sleep(_POD_WAIT_INTERVAL_SECONDS)
    raise BadGatewayError(f"MLflow pod in namespace '{namespace}' did not become Ready")


def _pod_error_reason(api: Any, namespace: str, deployment_name: str) -> str:
    from kubernetes import client

    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app=mlflow,deployment={deployment_name}",
    ).items
    for pod in pods:
        status = getattr(pod, "status", None)
        phase = (getattr(status, "phase", "") or "").strip()
        if phase in _POD_TERMINAL_PHASES:
            return phase
        for cs in getattr(status, "container_statuses", None) or []:
            waiting = getattr(getattr(cs, "state", None), "waiting", None)
            reason = (getattr(waiting, "reason", "") or "").strip()
            if reason in _POD_TERMINAL_REASONS:
                return reason
    return ""


def _ready_pod_name(api: Any, namespace: str, deployment_name: str) -> str:
    from kubernetes import client

    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app=mlflow,deployment={deployment_name}",
    ).items
    for pod in pods:
        phase = (getattr(getattr(pod, "status", None), "phase", "") or "").strip()
        name = (getattr(getattr(pod, "metadata", None), "name", "") or "").strip()
        conditions = getattr(getattr(pod, "status", None), "conditions", None) or []
        ready = any(
            getattr(condition, "type", None) == "Ready"
            and getattr(condition, "status", None) == "True"
            for condition in conditions
        )
        if phase == "Running" and ready and name:
            return name
    return ""


def _data_pvc_name(deployment_name: str) -> str:
    suffix = "-data"
    return f"{deployment_name[:63 - len(suffix)].rstrip('-')}{suffix}"


def _image_pull_secret_name(deployment_name: str) -> str:
    suffix = "-pull-secret"
    return f"{deployment_name[:63 - len(suffix)].rstrip('-')}{suffix}"


def _image_pull_secret_manifest(
    dockerconfigjson: str | None,
    namespace: str,
    deployment_name: str,
    q: Any,
) -> str:
    if not dockerconfigjson:
        return ""
    secret_name = _image_pull_secret_name(deployment_name)
    return (
        "apiVersion: v1\n"
        "kind: Secret\n"
        "metadata:\n"
        f"  name: {q(secret_name)}\n"
        f"  namespace: {q(namespace)}\n"
        "type: kubernetes.io/dockerconfigjson\n"
        "stringData:\n"
        f"  .dockerconfigjson: {q(dockerconfigjson)}\n"
        "---\n"
    )


def _image_pull_secret_reference(dockerconfigjson: str | None, deployment_name: str) -> str:
    if not dockerconfigjson:
        return ""
    return f"      imagePullSecrets:\n        - name: {_image_pull_secret_name(deployment_name)}\n"


def _api_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    if reason and body:
        details = f"{reason} - {body}"
    else:
        details = reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
