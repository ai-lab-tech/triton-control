"""Status checks for the global Argo Workflows server."""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.exceptions import ServiceUnavailableError
from app.schemas import ArgoWorkflowsStatusResponse
from app.services.workflows import auth
from app.services.workflows.config import get_config


def get_status() -> ArgoWorkflowsStatusResponse:
    config = get_config()
    if not config.enabled:
        return ArgoWorkflowsStatusResponse(
            enabled=False,
            ready=False,
            status="disabled",
            status_message="Argo Workflows is not enabled in the Triton Control Helm release.",
            namespace=config.namespace,
            service_name=config.service_name,
            base_path=config.base_path,
            service_url=config.server_url,
        )
    if not config.server_url:
        return ArgoWorkflowsStatusResponse(
            enabled=True,
            ready=False,
            status="misconfigured",
            status_message="ARGO_WORKFLOWS_SERVER_URL is not configured.",
            namespace=config.namespace,
            service_name=config.service_name,
            base_path=config.base_path,
            service_url=config.server_url,
        )
    try:
        namespace = quote(config.namespace or "triton-control", safe="")
        response = httpx.get(
            f"{config.server_url}/api/v1/workflows/{namespace}",
            params={"listOptions.limit": "1"}, headers=auth.authorization_headers(),
            timeout=5, follow_redirects=False, trust_env=False,
        )
        ready = response.status_code < 400
        message = (
            f"Argo Server responded with HTTP {response.status_code}."
            if ready
            else f"Argo Server is unavailable (HTTP {response.status_code})."
        )
        return ArgoWorkflowsStatusResponse(
            enabled=True,
            ready=ready,
            status="ready" if ready else "unavailable",
            status_message=message,
            namespace=config.namespace,
            service_name=config.service_name,
            base_path=config.base_path,
            service_url=config.server_url,
        )
    except (httpx.HTTPError, ServiceUnavailableError) as exc:
        return ArgoWorkflowsStatusResponse(
            enabled=True,
            ready=False,
            status="unavailable",
            status_message=f"Argo Server could not be reached: {exc}",
            namespace=config.namespace,
            service_name=config.service_name,
            base_path=config.base_path,
            service_url=config.server_url,
        )
