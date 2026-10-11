"""Approve outbound Triton destinations using operator configuration or managed Services."""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, urlunsplit

from kubernetes import client  # type: ignore[import-untyped]

from app.services.kubernetes_client import api_client

_DNS_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def approved_external_url(value: str) -> str:
    """Rebuild the authority from an exact administrator-approved host and port."""
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        raise ValueError("Invalid Triton endpoint URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Invalid Triton endpoint URL") from exc
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0
        or parsed.username is not None or parsed.password is not None
        or "%" in parsed.netloc or parsed.fragment
    ):
        raise ValueError("Triton endpoints require HTTP(S), a hostname, and no credentials or fragment")
    for entry in os.getenv("TRITON_ALLOWED_ENDPOINTS", "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            approved = urlsplit(f"http://{entry}")
            approved_port = approved.port
        except ValueError as exc:
            raise ValueError("Invalid TRITON_ALLOWED_ENDPOINTS configuration") from exc
        if (
            not approved.hostname or approved_port in {None, 0} or approved.username is not None
            or approved.password is not None or approved.path or approved.query or approved.fragment
            or "%" in approved.netloc or "*" in entry or "\\" in entry
            or any(character.isspace() for character in entry)
        ):
            raise ValueError("TRITON_ALLOWED_ENDPOINTS must contain exact host:port entries")
        if (parsed.hostname.lower(), port) == (approved.hostname.lower(), approved_port):
            authority = approved.netloc
            if parsed.port is None:
                authority = f"[{approved.hostname}]" if ":" in approved.hostname else approved.hostname
            return urlunsplit((parsed.scheme, authority, parsed.path, parsed.query, ""))
    raise ValueError("Endpoint is not approved; ask an administrator to configure TRITON_ALLOWED_ENDPOINTS")


def managed_service_urls(namespace: str, service_name: str, deployment_name: str) -> dict[str, str]:
    """Verify the backend-owned Service before constructing internal destinations."""
    if not all(_DNS_LABEL.fullmatch(value) for value in (namespace, service_name, deployment_name)):
        raise ValueError("Invalid managed Triton deployment identity")
    api = api_client()
    service = client.CoreV1Api(api).read_namespaced_service(service_name, namespace)
    deployment = client.AppsV1Api(api).read_namespaced_deployment(deployment_name, namespace)
    selector = {"app": "triton", "deployment": deployment_name}
    if (
        service.spec.type != "ClusterIP" or service.spec.external_name or service.spec.external_i_ps
        or service.spec.selector != selector
        or deployment.spec.selector.match_labels != selector
        or not any(container.name == "triton" for container in deployment.spec.template.spec.containers)
    ):
        raise ValueError("Service does not belong to the managed Triton deployment")
    ports = {port.name: port.port for port in service.spec.ports or [] if port.protocol in {None, "TCP"}}
    if "http" not in ports:
        raise ValueError("Managed Triton Service has no HTTP port")
    host = f"{service_name}.{namespace}.svc.cluster.local"
    urls = {"http": f"http://{host}:{ports['http']}"}
    if "metrics" in ports:
        urls["metrics"] = f"http://{host}:{ports['metrics']}/metrics"
    return urls
