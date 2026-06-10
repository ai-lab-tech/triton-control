"""Authenticated proxy helpers for per-user code-server workspaces."""

from __future__ import annotations

import asyncio
import ssl
from typing import Any, cast
from urllib.parse import quote, urlencode

import anyio
import websockets
from fastapi import Request, Response, WebSocket
from starlette.websockets import WebSocketDisconnect

from app.db.entities import CodeServerEntity
from app.exceptions import BadGatewayError
from app.services.kubernetes_client import api_client

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
_REQUEST_SKIP_HEADERS = _HOP_BY_HOP_HEADERS | {"host", "content-length", "accept-encoding"}
_RESPONSE_SKIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "content-length",
    "content-security-policy",
    "x-frame-options",
}


async def proxy_http(row: CodeServerEntity, path: str, request: Request) -> Response:
    """Proxy an authenticated browser request to the workspace's Kubernetes Service."""
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _REQUEST_SKIP_HEADERS
    }
    query_params = list(request.query_params.multi_items())
    return await anyio.to_thread.run_sync(
        _proxy_http_sync,
        row,
        path,
        request.method,
        headers,
        query_params,
        body,
    )


async def proxy_websocket(row: CodeServerEntity, path: str, websocket: WebSocket) -> None:
    """Bridge an authenticated browser WebSocket to the workspace through the Kubernetes API."""
    upstream_url, headers, ssl_context = _websocket_upstream(row, path, list(websocket.query_params.multi_items()))
    requested_protocols = _requested_subprotocols(websocket)
    upstream = None
    try:
        upstream = await websockets.connect(
            upstream_url,
            additional_headers=headers,
            ssl=ssl_context,
            subprotocols=cast(Any, requested_protocols or None),
            proxy=None,
            max_size=None,
        )
        await websocket.accept(subprotocol=upstream.subprotocol)
        await asyncio.gather(
            _browser_to_upstream(websocket, upstream),
            _upstream_to_browser(websocket, upstream),
        )
    except WebSocketDisconnect:
        return
    except Exception:
        if websocket.client_state.name != "DISCONNECTED":
            await websocket.close(code=1011)
    finally:
        if upstream is not None:
            await upstream.close()


def _proxy_http_sync(
    row: CodeServerEntity,
    path: str,
    method: str,
    headers: dict[str, str],
    query_params: list[tuple[str, str]],
    body: bytes,
) -> Response:
    from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

    api = api_client()
    encoded_path = quote(path.lstrip("/"), safe="/:@")
    resource_path = "/api/v1/namespaces/{namespace}/services/{name}/proxy/{path}"
    path_params = {
        "namespace": row.namespace,
        "name": row.service_name,
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
        raise BadGatewayError(f"Code-server proxy request failed: {exc}") from exc

    proxied_headers = {
        key: value
        for key, value in dict(response_headers).items()
        if key.lower() not in _RESPONSE_SKIP_HEADERS
    }
    return Response(content=content, status_code=status_code, headers=proxied_headers)


def _websocket_upstream(
    row: CodeServerEntity,
    path: str,
    query_params: list[tuple[str, str]],
) -> tuple[str, dict[str, str], ssl.SSLContext | None]:
    api = api_client()
    config = api.configuration
    host = config.host.rstrip("/")
    scheme = "wss" if host.startswith("https://") else "ws"
    base = host.removeprefix("https://").removeprefix("http://")
    encoded_path = quote(path.lstrip("/"), safe="/:@")
    query = urlencode(query_params, doseq=True)
    upstream_url = (
        f"{scheme}://{base}/api/v1/namespaces/{quote(row.namespace, safe='')}"
        f"/services/{quote(row.service_name, safe='')}/proxy/{encoded_path}"
    )
    if query:
        upstream_url = f"{upstream_url}?{query}"

    headers = dict(api.default_headers)
    api.update_params_for_auth(headers, [], ["BearerToken"])
    return upstream_url, headers, _ssl_context(config) if scheme == "wss" else None


def _ssl_context(config: object) -> ssl.SSLContext:
    verify_ssl = bool(getattr(config, "verify_ssl", True))
    ca_file = getattr(config, "ssl_ca_cert", None)
    cert_file = getattr(config, "cert_file", None)
    key_file = getattr(config, "key_file", None)
    if verify_ssl:
        context = ssl.create_default_context(cafile=ca_file or None)
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if cert_file:
        context.load_cert_chain(certfile=cert_file, keyfile=key_file or None)
    return context


def _requested_subprotocols(websocket: WebSocket) -> list[str]:
    header = websocket.headers.get("sec-websocket-protocol", "")
    return [part.strip() for part in header.split(",") if part.strip()]


async def _browser_to_upstream(websocket: WebSocket, upstream: websockets.ClientConnection) -> None:
    while True:
        message = await websocket.receive()
        message_type = message.get("type")
        if message_type == "websocket.disconnect":
            await upstream.close()
            return
        text = message.get("text")
        if text is not None:
            await upstream.send(text)
            continue
        data = message.get("bytes")
        if data is not None:
            await upstream.send(data)


async def _upstream_to_browser(websocket: WebSocket, upstream: websockets.ClientConnection) -> None:
    async for message in upstream:
        if isinstance(message, bytes):
            await websocket.send_bytes(message)
        else:
            await websocket.send_text(message)


def _api_error(exc: Exception) -> str:
    reason = (getattr(exc, "reason", "") or "").strip()
    body = (getattr(exc, "body", "") or "").strip()
    status = getattr(exc, "status", None)
    details = f"{reason} - {body}" if reason and body else reason or body or "Kubernetes API request failed"
    return f"Kubernetes API error {status}: {details}" if status else details
