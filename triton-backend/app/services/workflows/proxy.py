"""Authenticated streaming proxy for the global Argo Workflows UI and API."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, cast
from urllib.parse import quote, urlencode

import httpx
import websockets
from fastapi import Request, WebSocket
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketDisconnect

from app.exceptions import BadGatewayError
from app.services.workflows.config import get_config

logger = logging.getLogger(__name__)

_PROXY_TIMEOUT_SECONDS = 1800
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
    "authorization",
    "cookie",
}
_RESPONSE_SKIP_HEADERS = _HOP_BY_HOP_HEADERS | {
    "content-length",
    "content-encoding",
    "content-security-policy",
    "set-cookie",
    "x-frame-options",
}


async def proxy_http(path: str, request: Request) -> StreamingResponse:
    config = get_config()
    if not config.enabled or not config.server_url:
        raise BadGatewayError("Argo Workflows is not configured")

    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=_PROXY_TIMEOUT_SECONDS,
        trust_env=False,
    )
    target = _http_url(config.server_url, path, list(request.query_params.multi_items()))
    headers = {key: value for key, value in request.headers.items() if key.lower() not in _REQUEST_SKIP_HEADERS}
    headers["x-forwarded-prefix"] = config.base_path.rstrip("/")
    try:
        upstream_request = client.build_request(
            request.method,
            target,
            headers=headers,
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise BadGatewayError(f"Argo Workflows proxy request failed: {exc}") from exc

    response_headers = {
        key: value for key, value in upstream.headers.items() if key.lower() not in _RESPONSE_SKIP_HEADERS
    }
    location = response_headers.get("location")
    if location and location.startswith("/"):
        response_headers["location"] = f"{config.base_path.rstrip('/')}{location}"
    return StreamingResponse(
        _response_body(upstream),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(_close_upstream, upstream, client),
    )


async def proxy_websocket(path: str, websocket: WebSocket) -> None:
    config = get_config()
    if not config.enabled or not config.server_url:
        await websocket.close(code=1013)
        return

    upstream_url = _websocket_url(config.server_url, path, list(websocket.query_params.multi_items()))
    requested_protocols = _requested_subprotocols(websocket)
    upstream = None
    try:
        upstream = await websockets.connect(
            upstream_url,
            subprotocols=cast(Any, requested_protocols or None),
            proxy=None,
            max_size=None,
        )
        await websocket.accept(subprotocol=upstream.subprotocol)
        await _proxy_websocket_messages(websocket, upstream)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Argo Workflows WebSocket proxy failed")
        await _close_websocket(websocket, code=1011)
    finally:
        if upstream is not None:
            await upstream.close()


async def _response_body(response: httpx.Response) -> AsyncIterator[bytes]:
    async for chunk in response.aiter_raw():
        yield chunk


async def _close_upstream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    await response.aclose()
    await client.aclose()


def _http_url(server_url: str, path: str, query_params: list[tuple[str, str]]) -> str:
    encoded_path = quote(path.lstrip("/"), safe="/:@")
    query = urlencode(query_params, doseq=True)
    target = f"{server_url.rstrip('/')}/{encoded_path}"
    return f"{target}?{query}" if query else target


def _websocket_url(server_url: str, path: str, query_params: list[tuple[str, str]]) -> str:
    target = _http_url(server_url, path, query_params)
    if target.startswith("https://"):
        return target.replace("https://", "wss://", 1)
    return target.replace("http://", "ws://", 1)


def _requested_subprotocols(websocket: WebSocket) -> list[str]:
    header = websocket.headers.get("sec-websocket-protocol", "")
    return [part.strip() for part in header.split(",") if part.strip()]


async def _proxy_websocket_messages(websocket: WebSocket, upstream: websockets.ClientConnection) -> None:
    browser_to_upstream = asyncio.create_task(_browser_to_upstream(websocket, upstream))
    upstream_to_browser = asyncio.create_task(_upstream_to_browser(websocket, upstream))
    done, pending = await asyncio.wait(
        {browser_to_upstream, upstream_to_browser},
        return_when=asyncio.FIRST_COMPLETED,
    )
    try:
        for task in done:
            await task
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _browser_to_upstream(websocket: WebSocket, upstream: websockets.ClientConnection) -> None:
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            await upstream.close()
            return
        if message.get("text") is not None:
            await upstream.send(message["text"])
        elif message.get("bytes") is not None:
            await upstream.send(message["bytes"])


async def _upstream_to_browser(websocket: WebSocket, upstream: websockets.ClientConnection) -> None:
    try:
        async for message in upstream:
            if isinstance(message, bytes):
                await websocket.send_bytes(message)
            else:
                await websocket.send_text(message)
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        return
    except RuntimeError as exc:
        if _is_closed_websocket_send_error(exc):
            return
        raise


async def _close_websocket(websocket: WebSocket, *, code: int) -> None:
    if websocket.client_state.name == "DISCONNECTED":
        return
    try:
        await websocket.close(code=code)
    except RuntimeError as exc:
        if not _is_closed_websocket_send_error(exc):
            raise


def _is_closed_websocket_send_error(exc: RuntimeError) -> bool:
    message = str(exc)
    return (
        "Unexpected ASGI message 'websocket.send'" in message
        or "Unexpected ASGI message 'websocket.close'" in message
    )
