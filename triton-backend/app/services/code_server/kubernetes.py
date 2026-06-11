"""Kubernetes operations for per-user code-server workspaces."""

from __future__ import annotations

from typing import Any

from app.exceptions import BadGatewayError
from app.schemas import CreateCodeServerRequest
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster


def apply_code_server_resources(
    request: CreateCodeServerRequest,
    *,
    namespace: str,
    statefulset_name: str,
    service_name: str,
    secret_name: str,
) -> list[str]:
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
    from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]
    from kubernetes.utils.create_from_yaml import FailToCreateError  # type: ignore[import-untyped]

    try:
        api = api_client()
        _ensure_namespace(api, namespace)
        applied = []
        for manifest in _manifests(request, namespace, statefulset_name, service_name, secret_name):
            from kubernetes import utils  # type: ignore[import-untyped]

            utils.create_from_dict(api, data=manifest, namespace=namespace, verbose=False, apply=True)
            meta = manifest.get("metadata") or {}
            applied.append(f"{manifest.get('kind', 'Resource')}/{meta.get('name', 'unknown')}")
        return applied
    except ConfigException as exc:
        raise BadGatewayError("Kubernetes configuration could not be loaded") from exc
    except ApiException as exc:
        raise BadGatewayError(_api_error(exc)) from exc
    except FailToCreateError as exc:
        errs = getattr(exc, "api_exceptions", []) or []
        message = "; ".join(_api_error(e) for e in errs) or "Failed to apply Kubernetes resources"
        raise BadGatewayError(message) from exc
    except Exception as exc:
        raise BadGatewayError(f"Failed to apply code-server resources: {exc}") from exc


def delete_code_server_resources(
    *,
    namespace: str,
    statefulset_name: str,
    service_name: str,
    secret_name: str,
) -> str:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    apps = client.AppsV1Api(api_client())
    core = client.CoreV1Api(api_client())
    net = client.NetworkingV1Api(api_client())
    deleted: list[str] = []

    def _delete(callable_fn: Any, kind: str, name: str) -> None:
        if not (name or "").strip():
            return
        try:
            callable_fn(name=name, namespace=namespace)
            deleted.append(f"{kind}/{name}")
        except ApiException as exc:
            if exc.status != 404:
                raise

    _delete(apps.delete_namespaced_stateful_set, "StatefulSet", statefulset_name)
    _delete(core.delete_namespaced_service, "Service", service_name)
    _delete(core.delete_namespaced_secret, "Secret", secret_name)
    _delete(core.delete_namespaced_secret, "Secret", _image_pull_secret_name(statefulset_name))
    _delete(net.delete_namespaced_ingress, "Ingress", f"{statefulset_name}-ingress")
    return ", ".join(deleted) if deleted else "No code-server resources found to delete."


def read_status(namespace: str, statefulset_name: str) -> tuple[str, str]:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    selector = f"app=code-server,workspace={statefulset_name}"
    api = api_client()
    try:
        client.AppsV1Api(api).read_namespaced_stateful_set(
            name=statefulset_name,
            namespace=namespace,
        )
    except ApiException as exc:
        if exc.status == 404:
            return "missing", f"StatefulSet '{statefulset_name}' was not found in namespace '{namespace}'."
        raise
    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector=selector,
    ).items
    if not pods:
        return "creating", "Waiting for pod scheduling."
    messages = []
    ready_seen = False
    for pod in pods:
        pod_name = getattr(getattr(pod, "metadata", None), "name", "") or "unknown"
        phase = getattr(getattr(pod, "status", None), "phase", "") or "Unknown"
        conditions = getattr(getattr(pod, "status", None), "conditions", None) or []
        ready = any(
            getattr(c, "type", "") == "Ready" and getattr(c, "status", "") == "True"
            for c in conditions
        )
        ready_seen = ready_seen or ready
        messages.append(f"{pod_name}: {phase} ({'Ready' if ready else 'Not Ready'})")
    return ("ready" if ready_seen else "creating", "; ".join(messages))


def workspace_url(
    namespace: str,
    service_name: str,
) -> str:
    return f"http://{service_name}.{namespace}.svc.cluster.local:8080"


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


def _manifests(
    request: CreateCodeServerRequest,
    namespace: str,
    statefulset_name: str,
    service_name: str,
    secret_name: str,
) -> list[dict[str, Any]]:
    labels = {"app": "code-server", "workspace": statefulset_name}
    manifests = [
        _secret_manifest(namespace, secret_name),
        _statefulset_manifest(request, namespace, statefulset_name, service_name, labels),
        _service_manifest(namespace, service_name, labels),
    ]
    if request.dockerconfigjson:
        manifests.insert(0, _image_pull_secret_manifest(request, namespace, statefulset_name))
    return manifests


def _secret_manifest(namespace: str, secret_name: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": namespace},
        "type": "Opaque",
        "stringData": {"AUTH_MODE": "triton-control-proxy"},
    }


def _statefulset_manifest(
    request: CreateCodeServerRequest,
    namespace: str,
    statefulset_name: str,
    service_name: str,
    labels: dict[str, str],
) -> dict[str, Any]:
    pod_spec: dict[str, Any] = {
        "serviceName": service_name,
        "replicas": 1,
        "selector": {"matchLabels": labels},
        "template": {
            "metadata": {"labels": labels},
            "spec": {
                "containers": [
                    {
                        "name": "code-server",
                        "image": request.image,
                        "imagePullPolicy": "IfNotPresent",
                        "ports": [{"name": "http", "containerPort": 8080}],
                        "startupProbe": {
                            "httpGet": {"path": "/", "port": "http"},
                            "periodSeconds": 5,
                            "failureThreshold": 120,
                        },
                        "readinessProbe": {
                            "httpGet": {"path": "/", "port": "http"},
                            "periodSeconds": 5,
                            "failureThreshold": 3,
                        },
                        "command": ["/bin/sh", "-c"],
                        "args": [
                            (
                                "if ! command -v code-server >/dev/null 2>&1; then "
                                "curl -fsSL https://code-server.dev/install.sh | sh; "
                                "fi; "
                                "mkdir -p /workspace/.code-server/user-data/User "
                                "/workspace/.code-server/extensions; "
                                "printf '%s\n' "
                                "'{\"workbench.startupEditor\":\"none\","
                                f"\"workbench.colorTheme\":\"{request.theme}\"}}' "
                                "> /workspace/.code-server/user-data/User/settings.json; "
                                "if ! code-server "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "--list-extensions | grep -qx 'ms-python.python'; then "
                                "code-server "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "--install-extension ms-python.python || "
                                "echo 'Warning: failed to install ms-python.python extension' >&2; "
                                "fi; "
                                "if [ ! -e /workspace/README.md ]; then "
                                "printf '%s\n' '# Workspace' '' "
                                "'This persistent workspace is managed by Triton Control.' "
                                "> /workspace/README.md; "
                                "fi; "
                                "exec code-server --bind-addr 0.0.0.0:8080 --auth none "
                                "--user-data-dir /workspace/.code-server/user-data "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "/workspace"
                            ),
                        ],
                        "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                    },
                ],
            },
        },
        "volumeClaimTemplates": [
            {
                "metadata": {"name": "workspace"},
                "spec": {
                    "accessModes": ["ReadWriteOnce"],
                    "resources": {"requests": {"storage": request.storage_size}},
                },
            },
        ],
    }
    image_pull_secret = _image_pull_secret_name(statefulset_name)
    if request.dockerconfigjson:
        pod_spec["template"]["spec"]["imagePullSecrets"] = [{"name": image_pull_secret}]
    resources = _resources(request)
    if resources:
        pod_spec["template"]["spec"]["containers"][0]["resources"] = resources
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": statefulset_name, "namespace": namespace},
        "spec": pod_spec,
    }


def _service_manifest(namespace: str, service_name: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": service_name, "namespace": namespace},
        "spec": {
            "selector": labels,
            "ports": [{"name": "http", "port": 8080, "targetPort": 8080}],
        },
    }


def _image_pull_secret_manifest(
    request: CreateCodeServerRequest,
    namespace: str,
    statefulset_name: str,
) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": _image_pull_secret_name(statefulset_name), "namespace": namespace},
        "type": "kubernetes.io/dockerconfigjson",
        "stringData": {".dockerconfigjson": request.dockerconfigjson or ""},
    }


def _image_pull_secret_name(statefulset_name: str) -> str:
    suffix = "-pull-secret"
    return f"{statefulset_name[:63 - len(suffix)].rstrip('-')}{suffix}"


def _resources(request: CreateCodeServerRequest) -> dict[str, Any]:
    cpu_req = (request.cpu or "").strip()
    cpu_lim = (request.cpu_limit or "").strip() or cpu_req
    mem_req = (request.memory or "").strip()
    mem_lim = (request.memory_limit or "").strip() or mem_req
    resources: dict[str, Any] = {}
    if cpu_req or mem_req:
        resources["requests"] = {}
        if cpu_req:
            resources["requests"]["cpu"] = cpu_req
        if mem_req:
            resources["requests"]["memory"] = mem_req
    if cpu_lim or mem_lim:
        resources["limits"] = {}
        if cpu_lim:
            resources["limits"]["cpu"] = cpu_lim
        if mem_lim:
            resources["limits"]["memory"] = mem_lim
    return resources


def _api_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    details = f"{reason} - {body}" if reason and body else reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
