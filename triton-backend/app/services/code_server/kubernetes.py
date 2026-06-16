"""Kubernetes operations for per-user code-server workspaces."""

from __future__ import annotations

import base64
import html
import io
import json
import os
from pathlib import Path
from typing import Any
import zipfile

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
    _delete(core.delete_namespaced_config_map, "ConfigMap", _triton_deploy_extension_configmap_name(statefulset_name))
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
        _triton_deploy_extension_configmap(namespace, statefulset_name),
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
                "automountServiceAccountToken": False,
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "runAsGroup": 10001,
                    "fsGroup": 10001,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "code-server",
                        "image": request.image,
                        "imagePullPolicy": "IfNotPresent",
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "readOnlyRootFilesystem": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "env": [
                            {"name": "HOME", "value": "/workspace"},
                            {"name": "XDG_CONFIG_HOME", "value": "/workspace/.config"},
                            {"name": "XDG_DATA_HOME", "value": "/workspace/.local/share"},
                            {"name": "XDG_CACHE_HOME", "value": "/workspace/.cache"},
                            {"name": "VSCODE_RECONNECTION_GRACE_TIME", "value": "30000"},
                            {"name": "NODE_TLS_REJECT_UNAUTHORIZED", "value": "0"},
                        ],
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
                                "CODE_SERVER_BIN=/workspace/.local/bin/code-server; "
                                "if [ ! -x \"$CODE_SERVER_BIN\" ]; then "
                                "curl -fsSL https://code-server.dev/install.sh | "
                                "sh -s -- --method=standalone --prefix=/workspace/.local; "
                                "fi; "
                                "mkdir -p /workspace/.code-server/user-data/User "
                                "/workspace/.code-server/extensions; "
                                "rm -f /workspace/.code-server/extensions/.obsolete; "
                                "TRITON_DEPLOY_EXTENSION_VSIX=/tmp/triton-control-deploy.vsix; "
                                "base64 -d "
                                "/opt/triton-control/extensions/triton-deploy/"
                                "triton-control-deploy.vsix.b64 "
                                "> \"$TRITON_DEPLOY_EXTENSION_VSIX\"; "
                                "\"$CODE_SERVER_BIN\" "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "--install-extension \"$TRITON_DEPLOY_EXTENSION_VSIX\" "
                                "--force || "
                                "echo 'Warning: failed to install triton-control-deploy extension' >&2; "
                                "printf '%s\n' "
                                "'{\"workbench.startupEditor\":\"none\","
                                f"\"workbench.colorTheme\":\"{request.theme}\"}}' "
                                "> /workspace/.code-server/user-data/User/settings.json; "
                                "if ! \"$CODE_SERVER_BIN\" "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "--list-extensions | grep -qx 'ms-python.python'; then "
                                "\"$CODE_SERVER_BIN\" "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "--install-extension ms-python.python || "
                                "echo 'Warning: failed to install ms-python.python extension' >&2; "
                                "fi; "
                                "if [ ! -e /workspace/README.md ]; then "
                                "printf '%s\n' '# Workspace' '' "
                                "'This persistent workspace is managed by Triton Control.' "
                                "> /workspace/README.md; "
                                "fi; "
                                "exec \"$CODE_SERVER_BIN\" --bind-addr 0.0.0.0:8080 --auth none "
                                "--reconnection-grace-time 30 "
                                "--user-data-dir /workspace/.code-server/user-data "
                                "--extensions-dir /workspace/.code-server/extensions "
                                "/workspace"
                            ),
                        ],
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": "/workspace"},
                            {
                                "name": "triton-deploy-extension",
                                "mountPath": "/opt/triton-control/extensions/triton-deploy",
                                "readOnly": True,
                            },
                        ],
                    },
                ],
                "volumes": [
                    {
                        "name": "triton-deploy-extension",
                        "configMap": {"name": _triton_deploy_extension_configmap_name(statefulset_name)},
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


def _triton_deploy_extension_configmap(namespace: str, statefulset_name: str) -> dict[str, Any]:
    extension_dir = _triton_deploy_extension_dir()
    package_json = json.loads((extension_dir / "package.json").read_text(encoding="utf-8"))
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "data": {
            "triton-control-deploy.vsix.b64": _triton_deploy_extension_vsix_b64(extension_dir, package_json),
        },
        "immutable": False,
        "metadata": {
            "name": _triton_deploy_extension_configmap_name(statefulset_name),
            "namespace": namespace,
            "labels": {
                "app": "code-server",
                "workspace": statefulset_name,
                "extension": package_json.get("name", "triton-control-deploy"),
            },
        },
    }


def _triton_deploy_extension_vsix_b64(extension_dir: Path, package_json: dict[str, Any]) -> str:
    files = {
        "extension/package.json": (extension_dir / "package.json").read_text(encoding="utf-8"),
        "extension/extension.js": (extension_dir / "extension.js").read_text(encoding="utf-8"),
        "extension/README.md": (extension_dir / "README.md").read_text(encoding="utf-8"),
        "extension.vsixmanifest": _triton_deploy_vsix_manifest(package_json),
        "[Content_Types].xml": _vsix_content_types(),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _triton_deploy_vsix_manifest(package_json: dict[str, Any]) -> str:
    name = str(package_json.get("name", "triton-control-deploy"))
    version = str(package_json.get("version", "0.1.0"))
    publisher = str(package_json.get("publisher", "triton-control"))
    display_name = str(package_json.get("displayName", name))
    description = str(package_json.get("description", display_name))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<PackageManifest Version="2.0.0" '
        'xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" '
        'xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">\n'
        "  <Metadata>\n"
        f'    <Identity Language="en-US" Id="{html.escape(name)}" '
        f'Version="{html.escape(version)}" Publisher="{html.escape(publisher)}" />\n'
        f"    <DisplayName>{html.escape(display_name)}</DisplayName>\n"
        f'    <Description xml:space="preserve">{html.escape(description)}</Description>\n'
        "    <Categories>Other</Categories>\n"
        "  </Metadata>\n"
        "  <Installation>\n"
        '    <InstallationTarget Id="Microsoft.VisualStudio.Code" />\n'
        "  </Installation>\n"
        "  <Dependencies />\n"
        "  <Assets>\n"
        '    <Asset Type="Microsoft.VisualStudio.Code.Manifest" '
        'Path="extension/package.json" Addressable="true" />\n'
        "  </Assets>\n"
        "</PackageManifest>\n"
    )


def _vsix_content_types() -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
        '  <Default Extension="json" ContentType="application/json" />\n'
        '  <Default Extension="js" ContentType="application/javascript" />\n'
        '  <Default Extension="md" ContentType="text/markdown" />\n'
        '  <Default Extension="vsixmanifest" ContentType="text/xml" />\n'
        "</Types>\n"
    )


def _triton_deploy_extension_configmap_name(statefulset_name: str) -> str:
    suffix = "-triton-deploy-ext"
    return f"{statefulset_name[:63 - len(suffix)].rstrip('-')}{suffix}"


def _triton_deploy_extension_dir() -> Path:
    candidates = _triton_deploy_extension_dir_candidates()
    for candidate in candidates:
        if (candidate / "extension.js").is_file() and (candidate / "package.json").is_file():
            return candidate
    paths = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Triton deploy code-server extension files were not found in: {paths}")


def _triton_deploy_extension_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.getenv("TRITON_DEPLOY_CODE_SERVER_EXTENSION_DIR", "").strip()
    if configured:
        # Double-quoted Windows paths in .env can turn "\t" into a tab.
        configured_path = Path(configured.replace("\t", "\\t"))
        candidates.append(configured_path)
        candidates.append(configured_path / "triton-deploy")
    candidates.append(Path(__file__).resolve().parents[4] / "code-server-extensions" / "triton-deploy")
    candidates.append(Path("/opt/code-server-extensions/triton-deploy"))
    return candidates


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
