"""Backend credentials stay out of user requests and follow token rotation."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import workflows_api
from app.core.security import get_claims
from app.exceptions import ServiceUnavailableError
from app.services.workflows import auth, mlflow_delegation, proxy, status


class WorkflowsAuthTests(unittest.TestCase):
    def test_reads_rotated_projected_token_each_time(self):
        # Arrange
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            with patch.dict("os.environ", {"ARGO_WORKFLOWS_TOKEN_PATH": str(token_path)}):
                token_path.write_text("first-token\n")

                # Act
                initial_headers = auth.authorization_headers()
                token_path.write_text("rotated-token\n")
                rotated_headers = auth.authorization_headers()

                # Assert
                self.assertEqual(initial_headers, {"authorization": "Bearer first-token"})
                self.assertEqual(rotated_headers, {"authorization": "Bearer rotated-token"})

    def test_missing_token_fails_closed(self):
        # Arrange
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "missing-token"
            with patch.dict("os.environ", {"ARGO_WORKFLOWS_TOKEN_PATH": str(token_path)}):
                # Act
                with self.assertRaises(ServiceUnavailableError) as raised:
                    auth.authorization_headers()

                # Assert
                self.assertEqual(raised.exception.detail, "Argo backend ServiceAccount token is unavailable")

    def test_empty_and_invalid_tokens_fail_closed(self):
        for value in ["", "\n", "token\nInjected: value"]:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                # Arrange
                token_path = Path(directory) / "token"
                token_path.write_text(value)
                with patch.dict("os.environ", {"ARGO_WORKFLOWS_TOKEN_PATH": str(token_path)}):
                    # Act
                    with self.assertRaises(ServiceUnavailableError) as raised:
                        auth.authorization_headers()

                    # Assert
                    self.assertEqual(raised.exception.detail, "Argo backend ServiceAccount token is invalid")

    def test_no_credentials_does_not_forward_or_create_workflow_secret(self):
        # Arrange
        request = SimpleNamespace(
            method="POST", headers={}, query_params=SimpleNamespace(multi_items=lambda: []),
        )
        with (
            patch.object(proxy, "get_config", return_value=SimpleNamespace(
                enabled=True, server_url="http://argo", base_path="/api/workflows/proxy/",
            )),
            patch.object(auth, "authorization_headers", side_effect=ServiceUnavailableError("unavailable")),
            patch.object(proxy.httpx, "AsyncClient") as client,
            patch.object(proxy.mlflow_delegation, "prepare_submission") as prepare,
        ):
            # Act
            with self.assertRaises(ServiceUnavailableError):
                asyncio.run(proxy.proxy_http("api/v1/workflows/test", request, {"role": "member"}))

            # Assert
            client.assert_not_called()
            prepare.assert_not_called()

    def test_status_checks_authenticated_api_and_rejects_forbidden(self):
        # Arrange
        with (
            patch.object(status, "get_config", return_value=SimpleNamespace(
                enabled=True, server_url="http://argo", namespace="test", service_name="argo-server",
                base_path="/api/workflows/proxy/",
            )),
            patch.object(auth, "authorization_headers", return_value={"authorization": "Bearer backend-token"}),
            patch.object(status.httpx, "get", return_value=SimpleNamespace(status_code=403)) as get,
        ):
            # Act
            result = status.get_status()

        # Assert
        self.assertFalse(result.ready)
        self.assertEqual(get.call_args.args[0], "http://argo/api/v1/workflows/test")
        self.assertEqual(get.call_args.kwargs["headers"], {"authorization": "Bearer backend-token"})

    def test_websocket_uses_backend_credentials(self):
        # Arrange
        browser = SimpleNamespace(
            query_params=SimpleNamespace(multi_items=lambda: []), headers={"authorization": "Bearer forged"},
            accept=AsyncMock(), close=AsyncMock(), client_state=SimpleNamespace(name="CONNECTED"),
        )
        upstream = SimpleNamespace(subprotocol=None, close=AsyncMock())
        with (
            patch.object(proxy, "get_config", return_value=SimpleNamespace(enabled=True, server_url="http://argo")),
            patch.object(auth, "authorization_headers", return_value={"authorization": "Bearer backend-token"}),
            patch.object(proxy.websockets, "connect", AsyncMock(return_value=upstream)) as connect,
            patch.object(proxy, "_proxy_websocket_messages", AsyncMock()),
        ):
            # Act
            asyncio.run(proxy.proxy_websocket("api/v1/stream", browser))

        # Assert
        self.assertEqual(connect.call_args.kwargs["additional_headers"], {"authorization": "Bearer backend-token"})
        browser.accept.assert_awaited_once()
        upstream.close.assert_awaited_once()


class EmbeddedWorkflowSubmissionTests(unittest.TestCase):
    def test_embedded_ui_gateway_secret_mount_is_rejected_before_forwarding(self):
        # Arrange
        app = FastAPI()
        app.include_router(workflows_api.router)
        app.dependency_overrides[get_claims] = lambda: {"email": "alice@example.com", "role": "member"}
        workflow = {"workflow": {
            "metadata": {"generateName": "steal-token-"},
            "spec": {"entrypoint": "main", "templates": [{"name": "main", "container": {
                "image": "python:3.12-slim", "env": [{"name": "STOLEN_TOKEN", "valueFrom": {
                    "secretKeyRef": {"name": "mlflow-tracking-gateway", "key": "gateway-token"},
                }}],
            }}]},
        }}
        with (
            patch.object(proxy, "get_config", return_value=SimpleNamespace(
                enabled=True, server_url="http://argo", base_path="/api/workflows/proxy/",
            )),
            patch.object(auth, "authorization_headers", return_value={"authorization": "Bearer backend-token"}),
            patch.object(mlflow_delegation, "session_factory"),
            patch.object(mlflow_delegation, "require_user_entity", return_value=SimpleNamespace(id=7)),
            patch.object(mlflow_delegation.workflow_s3_credentials, "list_all", return_value=[]),
            patch.object(proxy.httpx, "AsyncClient") as upstream,
            patch.object(mlflow_delegation.client, "CoreV1Api") as core,
            TestClient(app) as client,
        ):
            # Act
            response = client.post("/api/workflows/proxy/api/v1/workflows/triton-control", json=workflow)

        # Assert
        self.assertEqual(response.status_code, 403)
        self.assertIn("linked S3 secrets", response.json()["detail"])
        upstream.assert_not_called()
        core.assert_not_called()
