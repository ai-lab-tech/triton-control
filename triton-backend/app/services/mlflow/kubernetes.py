"""Kubernetes operations and manifest rendering for singleton MLflow installs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError, BadRequestError
from app.schemas import InstallMlflowRequest
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster

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
        for manifest in _manifests(request, namespace, deployment_name, service_name):
            utils.create_from_dict(api, data=manifest, namespace=namespace, verbose=False, apply=True)
            meta = manifest.get("metadata") or {}
            applied.append(f"{manifest.get('kind', 'Resource')}/{meta.get('name', 'unknown')}")
        _wait_for_running_pod(api, namespace, deployment_name)
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
    return ", ".join(deleted) if deleted else "No MLflow resources found to delete."


def read_installation_readiness(namespace: str, deployment_name: str) -> tuple[bool, str]:
    """Return (ready, message) for MLflow pod in a namespace."""
    from kubernetes.config.config_exception import ConfigException

    try:
        api = _client()
        pod_name = _running_pod_name(api, namespace, deployment_name)
        if pod_name:
            return True, f"MLflow pod '{pod_name}' is Running."
        reason = _pod_error_reason(api, namespace, deployment_name)
        if reason:
            return False, f"MLflow pod not ready: {reason}"
        return False, "MLflow installation exists but pod is not Running yet."
    except ConfigException:
        return False, "Kubernetes configuration could not be loaded"
    except Exception as exc:
        return False, f"Failed to read MLflow readiness: {exc}"


def service_url(namespace: str, service_name: str) -> str:
    return f"http://{service_name}.{namespace}.svc.cluster.local:5000"


def _manifests(
    request: InstallMlflowRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
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
        image=q(request.image),
    )
    return [manifest for manifest in yaml.safe_load_all(rendered) if manifest]


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


def _wait_for_running_pod(api: Any, namespace: str, deployment_name: str) -> None:
    for _ in range(_POD_WAIT_ATTEMPTS):
        if _running_pod_name(api, namespace, deployment_name):
            return
        error = _pod_error_reason(api, namespace, deployment_name)
        if error:
            raise BadGatewayError(
                f"MLflow pod in namespace '{namespace}' failed to start: {error}"
            )
        time.sleep(_POD_WAIT_INTERVAL_SECONDS)
    raise BadGatewayError(f"MLflow pod in namespace '{namespace}' did not reach Running")


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


def _running_pod_name(api: Any, namespace: str, deployment_name: str) -> str:
    from kubernetes import client

    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector=f"app=mlflow,deployment={deployment_name}",
    ).items
    for pod in pods:
        phase = (getattr(getattr(pod, "status", None), "phase", "") or "").strip()
        name = (getattr(getattr(pod, "metadata", None), "name", "") or "").strip()
        if phase == "Running" and name:
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
