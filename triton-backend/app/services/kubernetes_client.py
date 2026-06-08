"""Shared Kubernetes API client loading for backend-managed workloads.

The backend uses ``KUBERNETES_KUBECONFIG_PATH`` when it is configured. When
that variable is empty, the backend is expected to run in Kubernetes and the
Python client loads the pod service-account credentials instead.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def api_client() -> Any:
    """Build an API client from the configured kubeconfig path or pod identity."""
    from kubernetes.client import ApiClient  # type: ignore[import-untyped]
    from kubernetes.config.incluster_config import load_incluster_config  # type: ignore[import-untyped]
    from kubernetes.config.kube_config import load_kube_config  # type: ignore[import-untyped]

    kubeconfig_path = (os.getenv("KUBERNETES_KUBECONFIG_PATH") or "").strip()
    if kubeconfig_path:
        load_kube_config(config_file=kubeconfig_path)
    else:
        load_incluster_config()
    return ApiClient()


def is_running_in_cluster() -> bool:
    """Return True when backend runs in a Kubernetes pod context."""
    service_host = (os.getenv("KUBERNETES_SERVICE_HOST") or "").strip()
    service_port = (os.getenv("KUBERNETES_SERVICE_PORT") or "").strip()
    ns_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    token_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    return bool(service_host and service_port and ns_file.exists() and token_file.exists())


def in_cluster_namespace() -> str:
    """Return current pod namespace, or empty string when unknown/not in-cluster."""
    env_ns = (os.getenv("POD_NAMESPACE") or "").strip()
    if env_ns:
        return env_ns
    ns_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
    try:
        return ns_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
