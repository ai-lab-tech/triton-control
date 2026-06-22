"""Environment-backed configuration for the global Argo Workflows server."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ArgoWorkflowsConfig:
    enabled: bool
    server_url: str
    namespace: str
    service_name: str
    base_path: str


def get_config() -> ArgoWorkflowsConfig:
    enabled = _env_bool("ARGO_WORKFLOWS_ENABLED", False)
    namespace = os.getenv("ARGO_WORKFLOWS_NAMESPACE", "").strip()
    service_name = os.getenv("ARGO_WORKFLOWS_SERVICE_NAME", "").strip()
    server_url = os.getenv("ARGO_WORKFLOWS_SERVER_URL", "").strip().rstrip("/")
    base_path = _normalize_base_path(os.getenv("ARGO_WORKFLOWS_BASE_PATH", "/api/workflows/proxy/"))
    return ArgoWorkflowsConfig(
        enabled=enabled,
        server_url=server_url,
        namespace=namespace,
        service_name=service_name,
        base_path=base_path,
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _normalize_base_path(value: str) -> str:
    path = f"/{(value or '').strip().strip('/')}/"
    return path if path != "//" else "/api/workflows/proxy/"
