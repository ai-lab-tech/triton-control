"""Database waits must leave the ASGI loop free to complete other requests."""

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request
from starlette.responses import Response

from app.api import development_api
from app.core import security


class AsyncDatabaseWaitTests(unittest.IsolatedAsyncioTestCase):
    def gated_lookup(self, gate, value):
        # Only the event loop can release this simulated pool wait. Running
        # the lookup on that loop would deadlock until this safety timeout.
        def lookup(*args, **kwargs):
            if not gate.wait(2):
                raise TimeoutError("Database wait blocked the request loop")
            return value
        return lookup

    async def test_authentication_pool_wait_leaves_loop_responsive(self):
        gate = threading.Event()
        claims = {"access_allowed": True}
        asyncio.get_running_loop().call_later(0.03, gate.set)
        request = Request({"type": "http", "session": {}})
        with patch.object(security, "extract_claims", AsyncMock(return_value={"sub": "test"})), patch.object(
            security, "_resolve_access_claims", self.gated_lookup(gate, claims)
        ):
            self.assertEqual(await security.get_claims(request, None), claims)

    async def test_http_and_websocket_ownership_waits_leave_loop_responsive(self):
        for websocket in (False, True):
            with self.subTest(websocket=websocket):
                gate = threading.Event()
                target = SimpleNamespace(namespace="test", service_name="workspace")
                asyncio.get_running_loop().call_later(0.03, gate.set)
                response = Response("ok")
                with patch.object(development_api, "_owned_proxy_target", self.gated_lookup(gate, target)), patch.object(
                    development_api.code_server_proxy, "proxy_http", AsyncMock(return_value=response)
                ) as http, patch.object(
                    development_api.code_server_proxy, "proxy_websocket", AsyncMock()
                ) as ws:
                    if websocket:
                        request = SimpleNamespace(session={"user": {"sub": "test"}}, close=AsyncMock())
                        await development_api.proxy_code_server_websocket(request, 1, "")
                        ws.assert_awaited_once_with(target, "", request)
                    else:
                        request = Request({"type": "http"})
                        result = await development_api.proxy_code_server(1, request, "", {"sub": "test"})
                        self.assertIs(result, response)
                        http.assert_awaited_once_with(target, "", request)
