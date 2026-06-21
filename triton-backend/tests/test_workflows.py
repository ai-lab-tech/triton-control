"""Tests for the global Argo Workflows integration."""

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
from kubernetes.client.rest import ApiException

from app.api import workflows_api
from app.exceptions import BadGatewayError, ConflictError, NotFoundError
from app.services.workflows import config, credentials, proxy, status


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
        with (
            patch.dict("os.environ", {"ARGO_WORKFLOWS_ENABLED": "false"}, clear=False),
            patch(
                "app.services.workflows.status.httpx.get",
            ) as get,
        ):
            result = status.get_status()

        self.assertEqual(result.status, "disabled")
        self.assertFalse(result.ready)
        get.assert_not_called()

    def test_Status_Ready_ReportsServerResponse(self) -> None:
        response = SimpleNamespace(status_code=200)
        with (
            patch.dict(
                "os.environ",
                {
                    "ARGO_WORKFLOWS_ENABLED": "true",
                    "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746",
                },
                clear=False,
            ),
            patch("app.services.workflows.status.httpx.get", return_value=response),
        ):
            result = status.get_status()

        self.assertTrue(result.ready)
        self.assertEqual(result.status, "ready")

    def test_Status_Unreachable_ReturnsUnavailable(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "ARGO_WORKFLOWS_ENABLED": "true",
                    "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746",
                },
                clear=False,
            ),
            patch(
                "app.services.workflows.status.httpx.get",
                side_effect=httpx.ConnectError("offline"),
            ),
        ):
            result = status.get_status()

        self.assertFalse(result.ready)
        self.assertEqual(result.status, "unavailable")

    def test_Status_MissingServerUrl_ReturnsMisconfigured(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ARGO_WORKFLOWS_ENABLED": "true",
                "ARGO_WORKFLOWS_SERVER_URL": "",
            },
            clear=False,
        ):
            result = status.get_status()

        self.assertEqual(result.status, "misconfigured")
        self.assertFalse(result.ready)

    def test_Status_ServerError_ReturnsUnavailable(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "ARGO_WORKFLOWS_ENABLED": "true",
                    "ARGO_WORKFLOWS_SERVER_URL": "http://argo:2746",
                },
                clear=False,
            ),
            patch(
                "app.services.workflows.status.httpx.get",
                return_value=SimpleNamespace(status_code=503),
            ),
        ):
            result = status.get_status()

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

    def test_ListWorkflowS3Credentials_MemberDelegates(self) -> None:
        expected = [
            workflows_api.WorkflowS3CredentialDTO(
                id=1,
                name="finance-prod",
                namespace="triton-control",
                secret_name="workflow-s3-finance-prod-abc123",
                access_key_id="AKIA123",
                created_at=datetime(2026, 1, 1),
                updated_at=datetime(2026, 1, 1),
            ),
        ]
        session = SimpleNamespace()
        with patch("app.api.workflows_api.credentials.list_credentials", return_value=expected) as mocked:
            result = workflows_api.list_workflow_s3_credentials(
                session=session,
                claims={"role": "member"},
            )

        mocked.assert_called_once_with(session)
        self.assertEqual(result, expected)

    def test_CreateWorkflowS3Credential_MemberDelegates(self) -> None:
        payload = workflows_api.CreateWorkflowS3CredentialRequest(
            name="finance-prod",
            access_key_id="AKIA123",
            secret_access_key="SECRET123",
        )
        expected = workflows_api.WorkflowS3CredentialDTO(
            id=2,
            name="finance-prod",
            namespace="triton-control",
            secret_name="workflow-s3-finance-prod-def456",
            access_key_id="AKIA123",
            created_at=datetime(2026, 1, 1),
            updated_at=datetime(2026, 1, 1),
        )
        session = SimpleNamespace()
        with patch("app.api.workflows_api.credentials.create_credential", return_value=expected) as mocked:
            result = workflows_api.create_workflow_s3_credential(
                payload,
                session=session,
                claims={"role": "member"},
            )

        mocked.assert_called_once_with(payload, session, {"role": "member"})
        self.assertEqual(result, expected)

    def test_CreateWorkflowS3Credential_StoresSecretOnlyInKubernetes(self) -> None:
        payload = workflows_api.CreateWorkflowS3CredentialRequest(
            name="finance-prod",
            access_key_id="AKIA123",
            secret_access_key="SECRET123",
        )
        created_at = datetime(2026, 1, 1)
        row = SimpleNamespace(
            id=2,
            name="finance-prod",
            namespace="triton-control",
            secret_name="workflow-s3-finance-prod-abc123",
            access_key_id="AKIA123",
            created_at=created_at,
            updated_at=created_at,
        )
        session = SimpleNamespace()
        user = SimpleNamespace(id=7)

        with (
            patch("app.services.workflows.credentials.require_user_entity", return_value=user),
            patch(
                "app.services.workflows.credentials._workflow_namespace",
                return_value="triton-control",
            ),
            patch(
                "app.services.workflows.credentials._secret_name",
                return_value="workflow-s3-finance-prod-abc123",
            ),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_name",
                return_value=None,
            ),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_secret_name",
                return_value=None,
            ),
            patch(
                "app.services.workflows.credentials._secret_exists",
                return_value=False,
            ),
            patch(
                "app.services.workflows.credentials._apply_secret",
            ) as apply_secret,
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.create",
                return_value=row,
            ) as create,
        ):
            result = credentials.create_credential(payload, session, {"role": "member"})

        apply_secret.assert_called_once_with(
            "triton-control",
            "workflow-s3-finance-prod-abc123",
            "AKIA123",
            "SECRET123",
        )
        stored_values = create.call_args.kwargs
        self.assertEqual(stored_values["access_key_id"], "AKIA123")
        self.assertNotIn("secret_access_key", stored_values)
        self.assertNotIn("secret_access_key_hash", stored_values)
        self.assertNotIn("secret_access_key_enc", stored_values)
        self.assertEqual(result.access_key_id, "AKIA123")

    def test_DeleteWorkflowS3Credential_MemberDelegates(self) -> None:
        expected = workflows_api.WorkflowS3CredentialDeleteResponse(
            status="deleted",
            message="Deleted workflow S3 credential 'finance-prod'.",
            id=5,
        )
        session = SimpleNamespace()
        with patch("app.api.workflows_api.credentials.delete_credential", return_value=expected) as mocked:
            result = workflows_api.delete_workflow_s3_credential(
                5,
                session=session,
                claims={"role": "member"},
            )

        mocked.assert_called_once_with(session, 5)
        self.assertEqual(result, expected)

    def test_CreateCredential_DuplicateName_RaisesConflict(self) -> None:
        payload = workflows_api.CreateWorkflowS3CredentialRequest(
            name="finance-prod",
            access_key_id="AKIA123",
            secret_access_key="SECRET123",
        )
        with (
            patch(
                "app.services.workflows.credentials.require_user_entity",
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_name",
                return_value=object(),
            ),
        ):
            with self.assertRaises(ConflictError):
                credentials.create_credential(payload, SimpleNamespace(), {})

    def test_CreateCredential_DatabaseFailure_DeletesCreatedSecret(self) -> None:
        payload = workflows_api.CreateWorkflowS3CredentialRequest(
            name="finance-prod",
            access_key_id="AKIA123",
            secret_access_key="SECRET123",
        )
        with (
            patch(
                "app.services.workflows.credentials.require_user_entity",
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "app.services.workflows.credentials._workflow_namespace",
                return_value="triton-control",
            ),
            patch(
                "app.services.workflows.credentials._secret_name",
                return_value="workflow-s3-finance",
            ),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_name",
                return_value=None,
            ),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_secret_name",
                return_value=None,
            ),
            patch(
                "app.services.workflows.credentials._secret_exists",
                return_value=False,
            ),
            patch("app.services.workflows.credentials._apply_secret"),
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.create",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch("app.services.workflows.credentials._delete_secret") as delete_secret,
        ):
            with self.assertRaises(RuntimeError):
                credentials.create_credential(payload, SimpleNamespace(), {})

        delete_secret.assert_called_once_with("triton-control", "workflow-s3-finance")

    def test_DeleteCredential_MissingRecord_RaisesNotFound(self) -> None:
        with patch(
            "app.services.workflows.credentials.workflow_s3_credentials.find_by_id",
            return_value=None,
        ):
            with self.assertRaises(NotFoundError):
                credentials.delete_credential(SimpleNamespace(), 99)

    def test_DeleteCredential_RemovesSecretAndRecord(self) -> None:
        row = SimpleNamespace(name="finance", namespace="ns", secret_name="secret")
        with (
            patch(
                "app.services.workflows.credentials.workflow_s3_credentials.find_by_id",
                return_value=row,
            ),
            patch("app.services.workflows.credentials._delete_secret") as delete_secret,
            patch("app.services.workflows.credentials.workflow_s3_credentials.delete") as delete_record,
        ):
            result = credentials.delete_credential(SimpleNamespace(), 7)

        delete_secret.assert_called_once_with("ns", "secret")
        delete_record.assert_called_once()
        self.assertEqual(result.id, 7)

    def test_CredentialHelpers_NormalizeNamesAndNamespace(self) -> None:
        with patch("app.services.workflows.credentials.secrets.token_hex", return_value="abc123"):
            self.assertEqual(
                credentials._secret_name(" Finance / Production "),
                "workflow-s3-finance-production-abc123",
            )
        with (
            patch(
                "app.services.workflows.credentials.get_config",
                return_value=SimpleNamespace(namespace=""),
            ),
            patch(
                "app.services.workflows.credentials.in_cluster_namespace",
                return_value="",
            ),
        ):
            self.assertEqual(credentials._workflow_namespace(), "triton-control")

    def test_SecretExists_HandlesFoundNotFoundAndApiFailure(self) -> None:
        core = MagicMock()
        with (
            patch("kubernetes.client.CoreV1Api", return_value=core),
            patch(
                "app.services.workflows.credentials.api_client",
                return_value=object(),
            ),
        ):
            self.assertTrue(credentials._secret_exists("ns", "secret"))
            core.read_namespaced_secret.side_effect = ApiException(status=404)
            self.assertFalse(credentials._secret_exists("ns", "secret"))
            core.read_namespaced_secret.side_effect = ApiException(
                status=500,
                reason="Server Error",
            )
            with self.assertRaises(BadGatewayError):
                credentials._secret_exists("ns", "secret")

    def test_ApplyAndDeleteSecret_MapKubernetesErrors(self) -> None:
        core = MagicMock()
        with (
            patch("kubernetes.client.CoreV1Api", return_value=core),
            patch(
                "app.services.workflows.credentials.api_client",
                return_value=object(),
            ),
        ):
            credentials._apply_secret("ns", "secret", "key", "value")
            body = core.create_namespaced_secret.call_args.kwargs["body"]
            self.assertEqual(body.string_data["access-key-id"], "key")

            core.create_namespaced_secret.side_effect = ApiException(status=409)
            with self.assertRaises(ConflictError):
                credentials._apply_secret("ns", "secret", "key", "value")

            core.delete_namespaced_secret.side_effect = ApiException(status=404)
            credentials._delete_secret("ns", "secret")
            core.delete_namespaced_secret.side_effect = ApiException(status=500)
            with self.assertRaises(BadGatewayError):
                credentials._delete_secret("ns", "secret")

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

    def test_ProxyHelpers_WebsocketUrlAndProtocols(self) -> None:
        self.assertEqual(
            proxy._websocket_url("https://argo", "stream", []),
            "wss://argo/stream",
        )
        websocket = SimpleNamespace(
            headers={"sec-websocket-protocol": "graphql-ws, chat"},
        )
        self.assertEqual(proxy._requested_subprotocols(websocket), ["graphql-ws", "chat"])

    def test_ProxyHttp_NotConfigured_RaisesBadGateway(self) -> None:
        request = SimpleNamespace()
        with patch(
            "app.services.workflows.proxy.get_config",
            return_value=SimpleNamespace(enabled=False, server_url=""),
        ):
            with self.assertRaises(BadGatewayError):
                asyncio.run(proxy.proxy_http("", request))

    def test_ProxyHttp_StreamsResponseAndFiltersHeaders(self) -> None:
        upstream = MagicMock()
        upstream.status_code = 302
        upstream.headers = {
            "location": "/workflows",
            "content-length": "12",
            "x-test": "yes",
        }

        async def chunks():
            yield b"hello"

        upstream.aiter_raw = chunks
        client = MagicMock()
        client.build_request.return_value = object()
        client.send = AsyncMock(return_value=upstream)
        client.aclose = AsyncMock()
        request = SimpleNamespace(
            method="GET",
            headers={"cookie": "private", "x-request": "ok"},
            query_params=SimpleNamespace(multi_items=lambda: [("x", "1")]),
            body=AsyncMock(return_value=b""),
        )
        with (
            patch(
                "app.services.workflows.proxy.get_config",
                return_value=SimpleNamespace(
                    enabled=True,
                    server_url="http://argo:2746",
                    base_path="/api/workflows/proxy/",
                ),
            ),
            patch("app.services.workflows.proxy.httpx.AsyncClient", return_value=client),
        ):
            response = asyncio.run(proxy.proxy_http("api/v1", request))
            body = asyncio.run(self._collect_stream(response))

        self.assertEqual(body, b"hello")
        self.assertEqual(response.headers["location"], "/api/workflows/proxy/workflows")
        self.assertEqual(response.headers["x-test"], "yes")

    @staticmethod
    async def _collect_stream(response):
        return b"".join([chunk async for chunk in response.body_iterator])

    def test_WebsocketMessageForwarders_HandleTextBytesAndDisconnect(self) -> None:
        browser = SimpleNamespace(
            receive=AsyncMock(
                side_effect=[
                    {"type": "websocket.receive", "text": "hello"},
                    {"type": "websocket.receive", "bytes": b"binary"},
                    {"type": "websocket.disconnect"},
                ]
            ),
            send_text=AsyncMock(),
            send_bytes=AsyncMock(),
        )
        upstream = SimpleNamespace(send=AsyncMock(), close=AsyncMock())
        asyncio.run(proxy._browser_to_upstream(browser, upstream))

        self.assertEqual(upstream.send.await_args_list[0].args, ("hello",))
        self.assertEqual(upstream.send.await_args_list[1].args, (b"binary",))
        upstream.close.assert_awaited_once()

    def test_ProxyWebsocket_UnauthenticatedIsClosed(self) -> None:
        websocket = SimpleNamespace(session={}, close=AsyncMock())

        asyncio.run(workflows_api.proxy_argo_workflows_websocket(websocket, "api/v1/stream"))

        websocket.close.assert_awaited_once_with(code=1008)
