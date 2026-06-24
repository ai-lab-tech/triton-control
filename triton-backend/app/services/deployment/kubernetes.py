"""Kubernetes operations and manifest rendering for Triton deployments.

Public surface: ``apply_deployment_resources``, ``delete_namespace``,
``read_deployment_logs``, and ``resolve_service_urls``. Helpers in this module
load the backend Kubernetes client configuration, render the YAML template,
apply resources, and translate Kubernetes API failures.

Here, rendering means substituting request and deployment values into the YAML
template so the resulting Kubernetes manifests can be parsed and applied.

Service-layer integration with the Kubernetes client; no HTTP handlers or DB
queries except through deployment record helper functions.
"""

from __future__ import annotations

import os
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import yaml  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError
from app.schemas import CreateDeploymentRequest
from app.services.deployment.records import record_deployment_failure, update_instance_after_apply
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster

_TEMPLATE = Path(__file__).with_name("triton_deployment.yaml")


def pending_url(
    namespace: str,
    service_name: str,
    ingress_host: str | None = None,
    ingress_scheme: str | None = None,
) -> str:
    host = (ingress_host or "").strip()
    if host:
        if not (host.startswith("http://") or host.startswith("https://")):
            host = f"{ingress_scheme or 'https'}://{host}"
        return host.rstrip("/")
    return f"http://{service_name}.{namespace}.svc.cluster.local:18000"


def pending_metrics_url(
    namespace: str,
    service_name: str,
    ingress_host: str | None = None,
    ingress_scheme: str | None = None,
) -> str:
    host = (ingress_host or "").strip()
    if host:
        if not (host.startswith("http://") or host.startswith("https://")):
            host = f"{ingress_scheme or 'https'}://{host}"
        return f"{host.rstrip('/')}/metrics"
    return f"http://{service_name}.{namespace}.svc.cluster.local:18002/metrics"


def pending_snapshot() -> dict[str, Any]:
    return {
        "server_metadata": {"name": "triton", "deployment_status": "deploying"},
        "model_names": [],
        "health_live": False,
        "health_ready": False,
        "checked_at": datetime.now(timezone.utc),
        "health_error": "Deployment is starting; health check pending.",
    }


def apply_deployment_resources(
    instance_id: int,
    request: CreateDeploymentRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
    secret_name: str,
    image: str,
) -> None:
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
    from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]
    from kubernetes.utils.create_from_yaml import FailToCreateError  # type: ignore[import-untyped]

    try:
        api = _client()
        _ensure_namespace(api, namespace)
        applied = []
        for manifest in _manifests(request, namespace, deployment_name, service_name, secret_name, image):
            from kubernetes import utils  # type: ignore[import-untyped]

            utils.create_from_dict(api, data=manifest, namespace=namespace, verbose=False, apply=True)
            meta = manifest.get("metadata") or {}
            applied.append(f"{manifest.get('kind', 'Resource')}/{meta.get('name', 'unknown')}")
        urls = _deployment_urls(request, api, namespace, service_name)
        update_instance_after_apply(
            instance_id,
            triton_url=urls["http"],
            metrics_url=urls["metrics"] if request.allow_metrics else "",
            applied_resources=applied,
        )
    except ConfigException:
        record_deployment_failure(instance_id, "Kubernetes configuration could not be loaded")
    except ApiException as exc:
        record_deployment_failure(instance_id, _api_error(exc))
    except FailToCreateError as exc:
        errs = getattr(exc, "api_exceptions", []) or []
        message = "; ".join(_api_error(e) for e in errs) or "Failed to apply Kubernetes resources"
        record_deployment_failure(instance_id, message)
    except Exception as exc:
        record_deployment_failure(instance_id, f"Deployment failed: {exc}")


def is_pod_ready(namespace: str, deployment_name: str) -> bool:
    """Return True when at least one pod for the deployment has Ready=True condition."""
    from kubernetes import client
    from kubernetes.config.config_exception import ConfigException

    try:
        api = _client()
        selector = f"app=triton,deployment={deployment_name}" if deployment_name else "app=triton"
        pods = client.CoreV1Api(api).list_namespaced_pod(
            namespace=namespace,
            label_selector=selector,
        ).items
        for pod in pods:
            conditions = getattr(getattr(pod, "status", None), "conditions", None) or []
            for condition in conditions:
                if (getattr(condition, "type", "") == "Ready"
                        and getattr(condition, "status", "") == "True"):
                    return True
    except ConfigException:
        return False
    except Exception:
        return False
    return False


def read_pod_statuses(namespace: str, deployment_name: str) -> list[str]:
    """Return a human-readable status string for each pod in the namespace."""
    from kubernetes import client
    from kubernetes.config.config_exception import ConfigException

    try:
        api = _client()
        selector = f"app=triton,deployment={deployment_name}" if deployment_name else "app=triton"
        pods = client.CoreV1Api(api).list_namespaced_pod(
            namespace=namespace,
            label_selector=selector,
        ).items
        result: list[str] = []
        for pod in pods:
            pod_name = getattr(getattr(pod, "metadata", None), "name", None) or "unknown"
            phase = getattr(getattr(pod, "status", None), "phase", None) or "Unknown"
            conditions = getattr(getattr(pod, "status", None), "conditions", None) or []
            ready = any(
                getattr(c, "type", "") == "Ready" and getattr(c, "status", "") == "True"
                for c in conditions
            )
            result.append(f"{pod_name}: {phase} ({'Ready' if ready else 'Not Ready'})")
        return result
    except ConfigException:
        return []
    except Exception:
        return []


def delete_namespace(namespace: str) -> str:
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
        raise BadGatewayError(f"Failed to delete Kubernetes namespace '{namespace}': {exc}") from exc


def delete_deployment_resources(
    namespace: str,
    deployment_name: str,
    service_name: str,
    secret_name: str,
) -> str:
    """Delete Triton deployment resources in-place without deleting namespace."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    apps = client.AppsV1Api(_client())
    core = client.CoreV1Api(_client())
    net = client.NetworkingV1Api(_client())
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

    _delete(apps.delete_namespaced_deployment, "Deployment", deployment_name)
    _delete(core.delete_namespaced_service, "Service", service_name)
    _delete(core.delete_namespaced_secret, "Secret", secret_name)
    _delete(core.delete_namespaced_secret, "Secret", _image_pull_secret_name(deployment_name))
    _delete(net.delete_namespaced_ingress, "Ingress", f"{deployment_name}-ingress")
    return ", ".join(deleted) if deleted else "No deployment resources found to delete."


def read_deployment_logs(namespace: str, deployment_name: str | None = None) -> str:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    v1 = client.CoreV1Api(_client())
    try:
        chunks = []
        deployment_name_value = (deployment_name or "").strip()
        selector = (
            f"app=triton,deployment={deployment_name_value}"
            if deployment_name_value
            else "app=triton"
        )
        for pod in v1.list_namespaced_pod(namespace=namespace, label_selector=selector).items:
            name = getattr(getattr(pod, "metadata", None), "name", "")
            if name:
                containers = getattr(getattr(pod, "spec", None), "containers", None) or []
                container_names = [
                    getattr(container, "name", "")
                    for container in containers
                    if getattr(container, "name", "")
                ] or [None]
                for container_name in container_names:
                    label = f"pod/{name}"
                    if container_name:
                        label = f"{label} container/{container_name}"
                    log = v1.read_namespaced_pod_log(
                        name=name,
                        namespace=namespace,
                        container=container_name,
                        tail_lines=300,
                    )
                    chunks.append(f"--- {label} ---\n{log}")
                    if container_name == "triton":
                        previous = _read_previous_pod_log(
                            v1, namespace=namespace, name=name, container=container_name
                        )
                        if previous:
                            chunks.append(f"--- {label} previous ---\n{previous}")
        return "\n\n".join(chunks).strip()
    except ApiException as exc:
        raise BadGatewayError(_api_error(exc)) from exc


def _read_previous_pod_log(v1: Any, *, namespace: str, name: str, container: str) -> str:
    from kubernetes.client.rest import ApiException

    try:
        return (
            v1.read_namespaced_pod_log(
                name=name,
                namespace=namespace,
                container=container,
                previous=True,
                tail_lines=300,
            )
            or ""
        )
    except ApiException as exc:
        if exc.status in {400, 404}:
            return ""
        raise


def _client() -> Any:
    return api_client()


def _ensure_namespace(api: Any, namespace: str) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    # In-cluster deployments should reuse the Triton Control namespace and must
    # not require cluster-scope namespace create permissions.
    if is_running_in_cluster() and in_cluster_namespace() == (namespace or "").strip():
        return

    v1 = client.CoreV1Api(api)
    try:
        v1.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    except ApiException as exc:
        if exc.status != 409:
            raise


def resolve_service_urls(api: Any, namespace: str, service_name: str) -> dict[str, str]:
    address = _wait_for_ingress_address(api, namespace, service_name)
    if address:
        return {"http": f"http://{address}", "metrics": f"http://{address}/metrics"}
    return _cluster_service_urls(api, namespace, service_name)


def resolve_deployment_service_urls(namespace: str, service_name: str) -> dict[str, str]:
    """Resolve externally reachable URLs for a deployed Triton service."""
    return resolve_service_urls(_client(), namespace, service_name)


def _deployment_urls(
    request: CreateDeploymentRequest, api: Any, namespace: str, service_name: str
) -> dict[str, str]:
    if request.ingress_host:
        host = request.ingress_host
        if not (host.startswith("http://") or host.startswith("https://")):
            host = f"{request.ingress_scheme or 'https'}://{host}"
        host = host.rstrip("/")
        return {"http": host, "metrics": f"{host}/metrics"}
    if not request.ingress_class_name:
        return _cluster_service_urls(api, namespace, service_name)
    return resolve_service_urls(api, namespace, service_name)


def _cluster_service_urls(api: Any, namespace: str, service_name: str) -> dict[str, str]:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    # Keep historical defaults as fallback in case service lookup fails.
    http_port = 18000
    metrics_port = 18002
    try:
        service = client.CoreV1Api(api).read_namespaced_service(name=service_name, namespace=namespace)
        for port in getattr(getattr(service, "spec", None), "ports", []) or []:
            name = str(getattr(port, "name", "") or "").strip().lower()
            number = int(getattr(port, "port", 0) or 0)
            if not number:
                continue
            if name == "http":
                http_port = number
            elif name == "metrics":
                metrics_port = number
    except (ApiException, ValueError, TypeError):
        pass
    return {
        "http": f"http://{service_name}.{namespace}.svc.cluster.local:{http_port}",
        "metrics": f"http://{service_name}.{namespace}.svc.cluster.local:{metrics_port}/metrics",
    }


def _wait_for_ingress_address(api: Any, namespace: str, service_name: str) -> str | None:
    for _ in range(10):
        if address := _ingress_address_for_service(api, namespace, service_name):
            return address
        time.sleep(1)
    return None


def _ingress_address_for_service(api: Any, namespace: str, service_name: str) -> str | None:
    from kubernetes import client

    networking = client.NetworkingV1Api(api)
    ingresses = networking.list_namespaced_ingress(namespace=namespace).items or []
    for ingress in ingresses:
        spec = getattr(ingress, "spec", None)
        for rule in getattr(spec, "rules", []) or []:
            http = getattr(rule, "http", None)
            if not http:
                continue
            for path in getattr(http, "paths", []) or []:
                backend = getattr(path, "backend", None)
                service = getattr(backend, "service", None)
                name = (getattr(service, "name", None) or "").strip()
                if name == service_name:
                    return _ingress_address(ingress)
    return None


def _ingress_address(ingress: Any) -> str | None:
    status = getattr(ingress, "status", None)
    load_balancer = getattr(status, "load_balancer", None)
    for item in getattr(load_balancer, "ingress", []) or []:
        if value := getattr(item, "ip", None) or getattr(item, "hostname", None):
            return str(value).strip()
    return None


def _manifests(
    request: CreateDeploymentRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
    secret_name: str,
    image: str,
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
        ingress_name=q(f"{deployment_name}-ingress"),
        ingress_host_rule=_ingress_host_rule(request.ingress_host, q),
        service_name=q(service_name),
        secret_name=q(secret_name),
        image=q(image),
        aws_ca_bundle=q("/etc/ssl/certs/ca-certificates.crt"),
        requirements_install_command=_requirements_install_command(request.requirements_txt),
        triton_server_args=_triton_server_args(request),
        s3_access_key=q(request.s3_access_key),
        s3_secret_key=q(request.s3_secret_key),
        s3_ca_certificate=q(request.s3_ca_certificate or ""),
        s3_region=q(request.s3_region),
        s3_ca_init_container_block=_s3_ca_init_container_block(request.s3_ca_certificate, image, q),
        s3_ca_volume_mount_block=_s3_ca_volume_mount_block(request.s3_ca_certificate),
        s3_ca_volume_item_block=_s3_ca_volume_item_block(request.s3_ca_certificate, secret_name),
        resources=_resources_block(request),
        ingress_class_name=request.ingress_class_name or "nginx",
    )
    manifests = [m for m in yaml.safe_load_all(rendered) if m]
    for manifest in manifests:
        if manifest.get("kind") == "Deployment" and request.repository_sync_mode != "direct":
            _add_local_s3_repository(manifest, request, secret_name)
    # Ingress is optional; avoid creating catch-all ingress entries when no host is provided.
    if not (request.ingress_host or "").strip():
        manifests = [m for m in manifests if str(m.get("kind", "")).lower() != "ingress"]
    return manifests


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


def _ingress_host_rule(ingress_host: str | None, q: Any) -> str:
    if not ingress_host:
        return ""
    return f"host: {q(ingress_host)}\n      "


def _triton_server_args(request: CreateDeploymentRequest) -> str:
    allow_metrics = str(request.allow_metrics).lower()
    args = [
        f"--model-repository={'/models' if request.repository_sync_mode != 'direct' else request.s3_url}",
        f"--model-control-mode={request.model_control_mode}",
        f"--allow-metrics={allow_metrics}",
        f"--allow-cpu-metrics={allow_metrics}",
        f"--allow-gpu-metrics={allow_metrics}",
        "--strict-readiness=false",
    ]
    if request.model_control_mode == "poll":
        args.append(f"--repository-poll-secs={request.repository_poll_secs}")
    if request.model_control_mode == "explicit":
        model_name = (request.model_name or "").strip() or "*"
        args.append(f"--load-model={model_name}")
    return " ".join(shlex.quote(arg) for arg in args)


def _add_local_s3_repository(
    deployment: dict[str, Any], request: CreateDeploymentRequest, secret_name: str
) -> None:
    """Attach the stable local repository and the selected S3 synchronization worker."""
    pod_spec = deployment["spec"]["template"]["spec"]
    triton = pod_spec["containers"][0]
    pod_spec.setdefault("volumes", []).append({"name": "model-repository", "emptyDir": {}})
    pod_spec["volumes"].append({"name": "s3-sync-staging", "emptyDir": {}})
    triton.setdefault("volumeMounts", []).append(
        {"name": "model-repository", "mountPath": "/models"}
    )

    sync_container = _s3_sync_container(request, secret_name)
    if request.repository_sync_mode == "init":
        pod_spec.setdefault("initContainers", []).append(sync_container)
        return

    pod_spec["containers"].append(sync_container)
    # Containers start concurrently. Do not let Triton scan a partial first sync.
    triton["args"][0] = (
        "until [ -f /models/.s3-sync-ready ]; do sleep 1; done\n" + triton["args"][0]
    )


def _s3_sync_container(request: CreateDeploymentRequest, secret_name: str) -> dict[str, Any]:
    source, endpoint = _aws_s3_source(request.s3_url)
    sync_command = (
        "aws s3 sync \"$S3_SOURCE\" /staging --delete --only-show-errors"
        + (" --endpoint-url \"$S3_ENDPOINT\"" if endpoint else "")
    )
    publish_command = (
        "rm -rf /models/.s3-sync-next\n"
        "mkdir -p /models/.s3-sync-next\n"
        "publish_model() {\n"
        "  source_dir=\"$1\"\n"
        "  fallback_name=\"$2\"\n"
        "  model_name=$(sed -n -E 's/^[[:space:]]*name[[:space:]]*:[[:space:]]*\"([^\"]+)\".*/\\1/p' "
        "\"$source_dir/config.pbtxt\" | head -n 1)\n"
        "  [ -n \"$model_name\" ] || model_name=\"$fallback_name\"\n"
        "  case \"$model_name\" in ''|'.'|'..'|*/*) echo 'Invalid Triton model name' >&2; exit 1;; esac\n"
        "  mkdir -p \"/models/.s3-sync-next/$model_name\"\n"
        "  cp -R \"$source_dir/.\" \"/models/.s3-sync-next/$model_name/\"\n"
        "}\n"
        "if [ -f /staging/config.pbtxt ]; then\n"
        "  publish_model /staging \"$(basename \"${S3_SOURCE%/}\")\"\n"
        "else\n"
        "  found_model=0\n"
        "  for source_dir in /staging/*; do\n"
        "    [ -d \"$source_dir\" ] || continue\n"
        "    [ -f \"$source_dir/config.pbtxt\" ] || continue\n"
        "    found_model=1\n"
        "    publish_model \"$source_dir\" \"$(basename \"$source_dir\")\"\n"
        "  done\n"
        "  [ \"$found_model\" -eq 1 ] || cp -R /staging/. /models/.s3-sync-next/\n"
        "fi\n"
        "find /models -mindepth 1 -maxdepth 1 ! -name .s3-sync-ready ! -name .s3-sync-next -exec rm -rf -- {} +\n"
        "cp -R /models/.s3-sync-next/. /models/\n"
        "rm -rf /models/.s3-sync-next\n"
        "find /models -depth -type d -empty -delete"
    )
    # vLLM reads these as host paths. Triton's native S3 repository otherwise
    # downloads into an opaque namespace, making relative values invalid.
    rewrite_command = (
        "find /models -type f -name model.json -exec sh -c '\n"
        "  for file do\n"
        "    dir=$(dirname \"$file\")\n"
        "    escaped_dir=$(printf \"%s\\n\" \"$dir\" | sed \"s/[\\\\&#]/\\\\\\\\&/g\")\n"
        "    sed -i -E \"s#(\\\"(model|tokenizer)\\\"[[:space:]]*:[[:space:]]*\\\")"
        "([^/][^\\\"]*)\\\"#\\1${escaped_dir}/\\3\\\"#g\" \"$file\"\n"
        "  done\n"
        "' sh {} +"
    )
    once = f"{sync_command}\n{publish_command}\n{rewrite_command}\ntouch /models/.s3-sync-ready"
    script = "set -eu\n" + once
    if request.repository_sync_mode == "sidecar":
        script += f"\nwhile sleep {request.repository_poll_secs}; do\n{once}\ndone"

    env: list[dict[str, Any]] = [
        {
            "name": "AWS_ACCESS_KEY_ID",
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "AWS_ACCESS_KEY_ID"}},
        },
        {
            "name": "AWS_SECRET_ACCESS_KEY",
            "valueFrom": {"secretKeyRef": {"name": secret_name, "key": "AWS_SECRET_ACCESS_KEY"}},
        },
        {"name": "AWS_DEFAULT_REGION", "value": request.s3_region},
        {"name": "S3_SOURCE", "value": source},
    ]
    if endpoint:
        env.append({"name": "S3_ENDPOINT", "value": endpoint})
    mounts: list[dict[str, Any]] = [
        {"name": "model-repository", "mountPath": "/models"},
        {"name": "s3-sync-staging", "mountPath": "/staging"},
    ]
    if request.s3_ca_certificate:
        env.append({"name": "AWS_CA_BUNDLE", "value": "/etc/ssl/certs/ca-certificates.crt"})
        mounts.append(
            {
                "name": "s3-ca-bundle",
                "mountPath": "/etc/ssl/certs/ca-certificates.crt",
                "subPath": "ca-certificates.crt",
                "readOnly": True,
            }
        )
    return {
        "name": "s3-model-sync",
        "image": _repository_sync_image(request),
        "imagePullPolicy": "IfNotPresent",
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": False,
            "capabilities": {"drop": ["ALL"]},
        },
        "env": env,
        "volumeMounts": mounts,
        "command": ["/bin/sh", "-c"],
        "args": [script],
    }


def _aws_s3_source(s3_url: str) -> tuple[str, str | None]:
    value = s3_url.removeprefix("s3://").strip("/")
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        scheme = "https" if parsed.scheme == "http" and parsed.port == 443 else parsed.scheme
        path = parsed.path.strip("/")
        return f"s3://{path}", f"{scheme}://{parsed.netloc}"
    return f"s3://{value}", None


def _repository_sync_image(request: CreateDeploymentRequest) -> str:
    return (
        (request.repository_sync_image or "").strip()
        or (os.getenv("TRITON_DEPLOY_S3_SYNC_IMAGE") or "").strip()
        or "amazon/aws-cli:2.22.35"
    )


def _resources_block(request: CreateDeploymentRequest) -> str:
    has_gpu = request.gpu_count is not None and request.gpu_count > 0
    cpu_req = (request.cpu or "").strip()
    cpu_lim = (request.cpu_limit or "").strip() or cpu_req
    mem_req = (request.memory or "").strip()
    mem_lim = (request.memory_limit or "").strip() or mem_req
    has_requests = bool(cpu_req or mem_req)
    has_limits = bool(cpu_lim or mem_lim or has_gpu)
    if not has_requests and not has_limits:
        return ""
    lines = ["          resources:"]
    if has_requests:
        lines.append("            requests:")
        if cpu_req:
            lines.append(f'              cpu: "{cpu_req}"')
        if mem_req:
            lines.append(f"              memory: {mem_req}")
    if has_limits:
        lines.append("            limits:")
        if cpu_lim:
            lines.append(f'              cpu: "{cpu_lim}"')
        if mem_lim:
            lines.append(f"              memory: {mem_lim}")
        if has_gpu:
            lines.append(f'              nvidia.com/gpu: "{request.gpu_count}"')
    return "\n".join(lines) + "\n"


def _requirements_install_command(requirements_txt: str | None) -> str:
    requirements = _requirements_lines(requirements_txt)
    if not requirements:
        return ""
    pip_args = " ".join(shlex.quote(requirement) for requirement in requirements)
    # In-container pip target used in the generated Kubernetes command, not host temp-file handling.
    target = "/tmp/triton-python-packages"  # nosec B108
    return (
        f"python3 -m pip install --no-cache-dir --target {target} {pip_args} && \\\n"
        f"              export PYTHONPATH={target}:${{PYTHONPATH:-}} && \\\n"
        "              "
    )


def _s3_ca_volume_mount_block(s3_ca_certificate: str | None) -> str:
    cert = (s3_ca_certificate or "").strip()
    if not cert:
        return ""
    return (
        "            - name: s3-ca-bundle\n"
        "              mountPath: /etc/ssl/certs/ca-certificates.crt\n"
        "              subPath: ca-certificates.crt\n"
        "              readOnly: true\n"
    )


def _s3_ca_init_container_block(s3_ca_certificate: str | None, image: str, q: Any) -> str:
    cert = (s3_ca_certificate or "").strip()
    if not cert:
        return ""
    return (
        "      initContainers:\n"
        "        - name: build-s3-ca-bundle\n"
        f"          image: {q(image)}\n"
        "          imagePullPolicy: IfNotPresent\n"
        "          securityContext:\n"
        "            runAsNonRoot: true\n"
        "            allowPrivilegeEscalation: false\n"
        "            readOnlyRootFilesystem: true\n"
        "            capabilities:\n"
        "              drop:\n"
        "                - ALL\n"
        "          command:\n"
        "            - /bin/bash\n"
        "            - -c\n"
        "          args:\n"
        "            - |\n"
        "              set -euo pipefail\n"
        "              cp /etc/ssl/certs/ca-certificates.crt /ca-bundle/ca-certificates.crt\n"
        "              printf '\\n' >> /ca-bundle/ca-certificates.crt\n"
        "              cat /s3-ca/s3-ca.crt >> /ca-bundle/ca-certificates.crt\n"
        "          volumeMounts:\n"
        "            - name: s3-ca-bundle\n"
        "              mountPath: /ca-bundle\n"
        "            - name: s3-ca-cert\n"
        "              mountPath: /s3-ca/s3-ca.crt\n"
        "              subPath: s3-ca.crt\n"
        "              readOnly: true\n"
    )


def _s3_ca_volume_item_block(s3_ca_certificate: str | None, secret_name: str) -> str:
    cert = (s3_ca_certificate or "").strip()
    if not cert:
        return ""
    return (
        "        - name: s3-ca-bundle\n"
        "          emptyDir: {}\n"
        "        - name: s3-ca-cert\n"
        "          secret:\n"
        f"            secretName: {secret_name}\n"
        "            items:\n"
        "              - key: S3_CA_CERTIFICATE\n"
        "                path: s3-ca.crt\n"
    )


def _requirements_lines(requirements_txt: str | None) -> list[str]:
    return [
        line.split(" #", 1)[0].strip()
        for line in (requirements_txt or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _api_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    if reason and body:
        details = f"{reason} - {body}"
    else:
        details = reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
