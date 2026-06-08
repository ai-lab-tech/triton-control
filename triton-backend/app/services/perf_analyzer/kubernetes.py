"""Kubernetes operations and manifest rendering for Perf Analyzer installs.

Public surface: ``apply_installation_resources`` and ``delete_namespace``.
Rendering substitutes request values into the YAML template before Kubernetes
manifests are parsed and applied. This service-layer module has no HTTP
handlers or database access.
"""

from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import Any, cast

import yaml  # type: ignore[import-untyped]

from app.exceptions import BadGatewayError, BadRequestError
from app.schemas import InstallPerfAnalyzerRequest
from app.services.kubernetes_client import api_client, in_cluster_namespace, is_running_in_cluster

_TEMPLATE = Path(__file__).with_name("perf_analyzer_deployment.yaml")
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
    request: InstallPerfAnalyzerRequest,
    *,
    namespace: str,
    deployment_name: str,
) -> list[str]:
    """Apply the Namespace-scoped resources needed for a Perf Analyzer pod."""
    from kubernetes import utils  # type: ignore[import-untyped]
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
    from kubernetes.config.config_exception import ConfigException  # type: ignore[import-untyped]
    from kubernetes.utils.create_from_yaml import FailToCreateError  # type: ignore[import-untyped]

    try:
        api = _client()
        _ensure_namespace(api, namespace)
        applied = []
        for manifest in _manifests(request, namespace, deployment_name):
            utils.create_from_dict(api, data=manifest, namespace=namespace, verbose=False, apply=True)
            meta = manifest.get("metadata") or {}
            applied.append(f"{manifest.get('kind', 'Resource')}/{meta.get('name', 'unknown')}")
        _wait_for_running_pod(api, namespace)
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
        raise BadGatewayError(f"Perf Analyzer installation failed: {exc}") from exc


def delete_namespace(namespace: str) -> str:
    """Request deletion of the namespace that owns the Perf Analyzer workload."""
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
        raise BadGatewayError(f"Failed to delete Perf Analyzer namespace '{namespace}': {exc}") from exc


def delete_installation_resources(namespace: str, deployment_name: str) -> str:
    """Delete Perf Analyzer deployment resources in-place without deleting namespace."""
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    apps = client.AppsV1Api(_client())
    deleted: list[str] = []
    try:
        apps.delete_namespaced_deployment(name=deployment_name, namespace=namespace)
        deleted.append(f"Deployment/{deployment_name}")
    except ApiException as exc:
        if exc.status != 404:
            raise BadGatewayError(_api_error(exc)) from exc
    try:
        client.CoreV1Api(_client()).delete_namespaced_secret(
            name=_image_pull_secret_name(deployment_name),
            namespace=namespace,
        )
        deleted.append(f"Secret/{_image_pull_secret_name(deployment_name)}")
    except ApiException as exc:
        if exc.status != 404:
            raise BadGatewayError(_api_error(exc)) from exc
    return ", ".join(deleted) if deleted else "No Perf Analyzer resources found to delete."


def read_installation_readiness(namespace: str) -> tuple[bool, str]:
    """Return (ready, message) for the Perf Analyzer pod in a namespace."""
    from kubernetes.config.config_exception import ConfigException

    try:
        api = _client()
        pod_name = _running_pod_name(api, namespace)
        if pod_name:
            return True, f"Perf Analyzer pod '{pod_name}' is Running."
        reason = _pod_error_reason(api, namespace)
        if reason:
            return False, f"Perf Analyzer pod not ready: {reason}"
        return False, "Perf Analyzer installation exists but pod is not Running yet."
    except ConfigException:
        return False, "Kubernetes configuration could not be loaded"
    except Exception as exc:
        return False, f"Failed to read Perf Analyzer readiness: {exc}"


def write_file_to_pod(namespace: str, path: str, content: str) -> None:
    """Write a string to a file inside the running Perf Analyzer pod via base64."""
    import base64

    from kubernetes import client
    from kubernetes.stream import stream  # type: ignore[import-untyped]

    api = _client()
    pod_name = _running_pod_name(api, namespace)
    if not pod_name:
        raise BadGatewayError(f"Perf Analyzer pod in namespace '{namespace}' is not Running")
    v1 = client.CoreV1Api(api)
    quoted_path = shlex.quote(path)
    b64_path = f"{path}.b64"
    quoted_b64_path = shlex.quote(b64_path)

    # Keep each shell command small to avoid hitting exec/argument length limits
    # for large JSON payloads (for example long XML test documents).
    init_out = str(
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["sh", "-c", f"> {quoted_b64_path}"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
    )
    if "command terminated with exit code" in init_out.lower():
        raise BadGatewayError(f"Failed to initialize temp input file in Perf Analyzer pod: {init_out}")

    stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=["sh", "-c", "command -v base64 >/dev/null 2>&1 || { echo 'base64 not found'; exit 127; }"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )

    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    chunk_size = 4096
    for index in range(0, len(b64), chunk_size):
        chunk = b64[index:index + chunk_size]
        append_out = str(
            stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=["sh", "-c", f"printf '%s' {shlex.quote(chunk)} >> {quoted_b64_path}"],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
        )
        if "command terminated with exit code" in append_out.lower():
            raise BadGatewayError(f"Failed to append input chunk in Perf Analyzer pod: {append_out}")

    decode_out = str(
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["sh", "-c", f"base64 -d {quoted_b64_path} > {quoted_path} && rm -f {quoted_b64_path}"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
    )
    if "command terminated with exit code" in decode_out.lower():
        raise BadGatewayError(f"Failed to decode input file in Perf Analyzer pod: {decode_out}")

    verify_out = str(
        stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=["sh", "-c", f"test -s {quoted_path} && echo ok || (echo 'missing input file'; exit 1)"],
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
    ).strip()
    if "ok" not in verify_out.lower():
        raise BadGatewayError(
            f"Input file was not created in Perf Analyzer pod at {path}. Verification output: {verify_out}"
        )

    stream(
        v1.connect_get_namespaced_pod_exec,
        pod_name,
        namespace,
        command=["sh", "-c", f"chmod 600 {quoted_path} || true"],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
    )


def exec_running_pod(namespace: str, command: list[str]) -> str:
    """Execute a command in the running Perf Analyzer pod and return output."""
    from kubernetes import client
    from kubernetes.stream import stream

    api = _client()
    pod_name = _running_pod_name(api, namespace)
    if not pod_name:
        raise BadGatewayError(f"Perf Analyzer pod in namespace '{namespace}' is not Running")

    v1 = client.CoreV1Api(api)
    cmdline = " ".join(shlex.quote(arg) for arg in command)
    marker = "__PA_EXIT_CODE__:"
    wrapped_command = f"{cmdline}; rc=$?; echo {marker}$rc"
    try:
        output = str(
            stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=["sh", "-c", wrapped_command],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
        )
        if marker not in output:
            raise BadGatewayError(
                "Perf Analyzer execution did not return an exit marker. "
                f"Output:\n{output}"
            )
        body, _, tail = output.rpartition(marker)
        exit_code_raw = (tail or "").strip().splitlines()[0].strip() if tail else ""
        run_output = body.rstrip()
        if exit_code_raw and exit_code_raw != "0":
            raise BadGatewayError(
                f"Perf Analyzer execution failed with exit code {exit_code_raw}. Output:\n{run_output}"
            )
        return run_output
    except BadGatewayError:
        raise
    except Exception as exc:
        raise BadGatewayError(f"Perf Analyzer execution failed: {exc}") from exc


def _manifests(
    request: InstallPerfAnalyzerRequest,
    namespace: str,
    deployment_name: str,
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
        image=q(request.image),
    )
    return [manifest for manifest in yaml.safe_load_all(rendered) if manifest]


def _client() -> Any:
    return api_client()


def _ensure_namespace(api: Any, namespace: str) -> None:
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    # In-cluster installs should reuse the Triton Control namespace and must
    # not require cluster-scope namespace create permissions.
    if is_running_in_cluster() and in_cluster_namespace() == (namespace or "").strip():
        return

    v1 = client.CoreV1Api(api)
    try:
        v1.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace)))
    except ApiException as exc:
        if exc.status != 409:
            raise


def _wait_for_running_pod(api: Any, namespace: str) -> None:
    for _ in range(_POD_WAIT_ATTEMPTS):
        if _has_running_pod(api, namespace):
            return
        error = _pod_error_reason(api, namespace)
        if error:
            raise BadGatewayError(
                f"Perf Analyzer pod in namespace '{namespace}' failed to start: {error}"
            )
        time.sleep(_POD_WAIT_INTERVAL_SECONDS)
    raise BadGatewayError(f"Perf Analyzer pod in namespace '{namespace}' did not reach Running")


def _pod_error_reason(api: Any, namespace: str) -> str:
    from kubernetes import client

    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector="app=perf-analyzer",
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


def _has_running_pod(api: Any, namespace: str) -> bool:
    return bool(_running_pod_name(api, namespace))


def _running_pod_name(api: Any, namespace: str) -> str:
    from kubernetes import client

    pods = client.CoreV1Api(api).list_namespaced_pod(
        namespace=namespace,
        label_selector="app=perf-analyzer",
    ).items
    for pod in pods:
        phase = (getattr(getattr(pod, "status", None), "phase", "") or "").strip()
        name = (getattr(getattr(pod, "metadata", None), "name", "") or "").strip()
        if phase == "Running" and name:
            return name
    return ""


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
