"""Authenticated proxy helpers for embedded singleton MLflow UI and API."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import anyio
import httpx
from fastapi import Request, Response

from app.exceptions import BadGatewayError
from app.services.kubernetes_client import api_client, is_running_in_cluster
from app.services.mlflow import config, installer

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_REQUEST_SKIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "host",
    "content-length",
    "accept-encoding",
    "cookie",
    # Prevent Kubernetes service proxy from turning browser cache validation
    # requests into ApiException(304) errors.
    "if-none-match",
    "if-modified-since",
}
_RESPONSE_SKIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "content-length",
    "content-encoding",
    "content-security-policy",
    "set-cookie",
    "x-frame-options",
}


async def proxy_http(path: str, request: Request, session: Any) -> Response:
    """Proxy authenticated request to the singleton MLflow server."""
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _REQUEST_SKIP_HEADERS
    }
    headers["x-forwarded-prefix"] = config.base_path().rstrip("/")
    query_params = list(request.query_params.multi_items())
    server_url = installer.get_proxy_server_url(session)
    return await anyio.to_thread.run_sync(
        _proxy_http_sync,
        server_url,
        path,
        request.method,
        headers,
        query_params,
        body,
    )


def _proxy_http_sync(
    server_url: str,
    path: str,
    method: str,
    headers: dict[str, str],
    query_params: list[tuple[str, str]],
    body: bytes,
) -> Response:
    if is_running_in_cluster():
        return _direct_proxy_http_sync(server_url, path, method, headers, query_params, body)
    return _kubernetes_proxy_http_sync(server_url, path, method, headers, query_params, body)


def _direct_proxy_http_sync(
    server_url: str,
    path: str,
    method: str,
    headers: dict[str, str],
    query_params: list[tuple[str, str]],
    body: bytes,
) -> Response:
    target = _http_url(server_url, path, query_params)
    try:
        with httpx.Client(follow_redirects=False, timeout=120, trust_env=False) as client:
            upstream = client.request(
                method,
                target,
                headers=headers,
                content=body if body else None,
            )
    except httpx.HTTPError as exc:
        raise BadGatewayError(f"MLflow direct proxy request failed: {exc}") from exc

    proxied_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _RESPONSE_SKIP_HEADERS
    }
    _rewrite_location_header(proxied_headers, server_url)
    return Response(content=upstream.content, status_code=upstream.status_code, headers=proxied_headers)


def _kubernetes_proxy_http_sync(
    server_url: str,
    path: str,
    method: str,
    headers: dict[str, str],
    query_params: list[tuple[str, str]],
    body: bytes,
) -> Response:
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

    namespace, service_name = _service_identity(server_url)
    api = api_client()
    encoded_path = quote(path.lstrip("/"), safe="/:@")
    resource_path = "/api/v1/namespaces/{namespace}/services/{name}/proxy/{path}"
    path_params = {
        "namespace": namespace,
        "name": f"{service_name}:http",
        "path": encoded_path,
    }
    try:
        upstream, status_code, response_headers = api.call_api(
            resource_path,
            method.upper(),
            path_params=path_params,
            query_params=query_params,
            header_params=headers,
            body=body if body else None,
            response_type="file",
            auth_settings=["BearerToken"],
            _return_http_data_only=False,
            _preload_content=False,
            _request_timeout=120,
        )
        content = upstream.data
        upstream.release_conn()
    except ApiException as exc:
        raise BadGatewayError(_api_error(exc)) from exc
    except Exception as exc:
        raise BadGatewayError(f"MLflow proxy request failed: {exc}") from exc

    proxied_headers = {
        key: value
        for key, value in dict(response_headers).items()
        if key.lower() not in _RESPONSE_SKIP_HEADERS
    }
    _rewrite_location_header(proxied_headers, server_url)
    return Response(content=content, status_code=status_code, headers=proxied_headers)


def _http_url(server_url: str, path: str, query_params: list[tuple[str, str]]) -> str:
    encoded_path = quote(path.lstrip("/"), safe="/:@")
    query = urlencode(query_params, doseq=True)
    target = f"{server_url.rstrip('/')}/{encoded_path}"
    return f"{target}?{query}" if query else target


def _rewrite_location_header(headers: dict[str, str], server_url: str) -> None:
    location = headers.get("location")
    if not location:
        return
    base_path = config.base_path().rstrip("/")
    if location.startswith("/"):
        headers["location"] = f"{base_path}{location}"
        return
    server = server_url.rstrip("/")
    if location.startswith(server):
        suffix = location[len(server):]
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        headers["location"] = f"{base_path}{suffix}"


def _service_identity(server_url: str) -> tuple[str, str]:
    split = urlsplit(server_url)
    host = (split.hostname or "").strip()
    host_parts = host.split(".")
    if len(host_parts) < 2:
        raise BadGatewayError("MLflow service URL is invalid for Kubernetes proxying")
    return host_parts[1], host_parts[0]


def _api_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    details = f"{reason} - {body}" if reason and body else reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
