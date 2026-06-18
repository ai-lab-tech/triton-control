"""Tests for the global Argo Workflows integration."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.api import workflows_api
from app.services.workflows import config, proxy, status


class WorkflowsTests(unittest.TestCase):
    def test_Config_NormalizesBasePath(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ARGO_WORKFLOWS_ENABLED": "true",
                "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746/",
                "ARGO_WORKFLOWS_BASE_PATH": "api/workflows/proxy",
            },
            clear=False,
        ):
            result = config.get_config()

        self.assertTrue(result.enabled)
        self.assertEqual(result.server_url, "http://argo:2746")
        self.assertEqual(result.base_path, "/api/workflows/proxy/")

    def test_Status_Disabled_DoesNotProbe(self) -> None:
        with patch.dict("os.environ", {"ARGO_WORKFLOWS_ENABLED": "false"}, clear=False), patch(
            "app.services.workflows.status.httpx.get",
        ) as get:
            result = status.get_status()

        self.assertEqual(result.status, "disabled")
        self.assertFalse(result.ready)
        get.assert_not_called()

    def test_Status_Ready_ReportsServerResponse(self) -> None:
        response = SimpleNamespace(status_code=200)
        with patch.dict(
            "os.environ",
            {
                "ARGO_WORKFLOWS_ENABLED": "true",
                "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746",
            },
            clear=False,
        ), patch("app.services.workflows.status.httpx.get", return_value=response):
            result = status.get_status()

        self.assertTrue(result.ready)
        self.assertEqual(result.status, "ready")

    def test_Status_Unreachable_ReturnsUnavailable(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ARGO_WORKFLOWS_ENABLED": "true",
                "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746",
            },
            clear=False,
        ), patch(
            "app.services.workflows.status.httpx.get",
            side_effect=httpx.ConnectError("offline"),
        ):
            result = status.get_status()

        self.assertFalse(result.ready)
        self.assertEqual(result.status, "unavailable")

    def test_ApiStatus_ViewerIsRejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            workflows_api.get_argo_workflows_status(claims={"role": "viewer"})

        self.assertEqual(raised.exception.status_code, 403)

    def test_ApiStatus_MemberDelegates(self) -> None:
        expected = status.ArgoWorkflowsStatusResponse(
            enabled=True,
            ready=True,
            status="ready",
            status_message="ok",
            namespace="tax-argo",
            service_name="argo-server",
            base_path="/api/workflows/proxy/",
        )
        with patch("app.api.workflows_api.status.get_status", return_value=expected):
            result = workflows_api.get_argo_workflows_status(claims={"role": "member"})

        self.assertEqual(result, expected)

    def test_ProxyUrl_PreservesPathAndQuery(self) -> None:
        result = proxy._http_url(
            "http://argo:2746",
            "api/v1/workflows/tax-argo",
            [("listOptions.labelSelector", "app=test")],
        )

        self.assertEqual(
            result,
            "http://argo:2746/api/v1/workflows/tax-argo?listOptions.labelSelector=app%3Dtest",
        )

    def test_ProxyWebsocket_UnauthenticatedIsClosed(self) -> None:
        websocket = SimpleNamespace(session={}, close=AsyncMock())

        asyncio.run(workflows_api.proxy_argo_workflows_websocket(websocket, "api/v1/stream"))

        websocket.close.assert_awaited_once_with(code=1008)
