"""Managed workspace Argo credentials and owner scope, using Arrange/Act/Assert."""

import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response, StreamingResponse

from app.api import workflows_api
from app.core import token_extractor
from app.core.security import get_claims
from app.db.entities import CodeServerEntity, UserEntity
from app.exceptions import ForbiddenError, UnauthorizedError
from app.services.development import kubernetes as workspace_k8s
from app.services.mlflow import tracking
from app.services.workflows import mlflow_delegation, proxy, workspace_access


class WorkspaceArgoIdentityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="alice@example.com", name="Alice", role="member", is_active=True))
            session.add(UserEntity(id=8, email="bob@example.com", name="Bob", role="member", is_active=True))
            for owner, name in [(7, "alice-workspace"), (8, "bob-workspace")]:
                session.add(CodeServerEntity(
                    owner_user_id=owner, name=name, namespace="control", statefulset_name=name,
                    service_name=f"{name}-svc", secret_name=f"{name}-secret", image="python:3.12",
                ))
            session.commit()
        self.addCleanup(self.engine.dispose)
        self.secrets = {
            f"{name}-secret": SimpleNamespace(data={
                workspace_access.TOKEN_KEY: base64.b64encode(token.encode()).decode(),
                tracking.TRACKING_TOKEN_KEY: base64.b64encode(b"mlflow-token").decode(),
            }) for name, token in [("alice-workspace", "alice-token"), ("bob-workspace", "bob-token")]
        }
        self.session_patch = patch.object(workspace_access, "session_factory", side_effect=lambda: Session(self.engine))
        self.session_patch.start()
        self.addCleanup(self.session_patch.stop)
        core_patch = patch.object(workspace_access.client, "CoreV1Api")
        self.core = core_patch.start().return_value
        self.addCleanup(core_patch.stop)
        self.core.read_namespaced_secret.side_effect = lambda name, namespace: self.secrets[name]
        api_patch = patch.object(workspace_access, "api_client")
        api_patch.start()
        self.addCleanup(api_patch.stop)

    def test_each_workspace_resolves_its_own_user(self):
        # Arrange
        workspaces = [("alice-workspace", "alice-token"), ("bob-workspace", "bob-token")]

        # Act
        identities = [workspace_access.authenticate_workspace("control", name, token) for name, token in workspaces]

        # Assert
        self.assertEqual([claims["email"] for claims in identities], ["alice@example.com", "bob@example.com"])
        self.assertEqual([claims["user_id"] for claims in identities], [7, 8])
        self.assertEqual(identities[0]["token_use"], "workspace_argo")

    def test_another_workspace_token_and_mlflow_token_are_rejected(self):
        for token in ["bob-token", "mlflow-token", "", "x" * 257]:
            with self.subTest(token=token):
                # Arrange
                name = "alice-workspace"

                # Act
                with self.assertRaises(UnauthorizedError) as raised:
                    workspace_access.authenticate_workspace("control", name, token)

                # Assert
                self.assertEqual(raised.exception.status_code, 401)

    def test_deleted_workspace_revokes_access(self):
        # Arrange
        with Session(self.engine) as session:
            workspace = session.get(CodeServerEntity, 1)
            session.delete(workspace)
            session.commit()

        # Act
        with self.assertRaises(UnauthorizedError) as raised:
            workspace_access.authenticate_workspace("control", "alice-workspace", "alice-token")

        # Assert
        self.assertEqual(raised.exception.status_code, 401)
        self.core.read_namespaced_secret.assert_not_called()

    def test_deleted_secret_revokes_access(self):
        # Arrange
        self.core.read_namespaced_secret.side_effect = ApiException(status=404)

        # Act
        with self.assertRaises(UnauthorizedError) as raised:
            workspace_access.authenticate_workspace("control", "alice-workspace", "alice-token")

        # Assert
        self.assertEqual(raised.exception.status_code, 401)

    def test_disabled_owner_or_viewer_role_revokes_access(self):
        for active, role in [(False, "member"), (True, "viewer")]:
            with self.subTest(active=active, role=role):
                # Arrange
                with Session(self.engine) as session:
                    owner = session.get(UserEntity, 7)
                    owner.is_active, owner.role = active, role
                    session.add(owner)
                    session.commit()

                # Act
                with self.assertRaises(ForbiddenError) as raised:
                    workspace_access.authenticate_workspace("control", "alice-workspace", "alice-token")

                # Assert
                self.assertEqual(raised.exception.status_code, 403)


class WorkspaceArgoApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-session-key")
        app.include_router(workflows_api.router)
        # A browser identity must never substitute for the managed credential.
        app.dependency_overrides[get_claims] = lambda: {"email": "admin@example.com", "role": "admin"}
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.base = "/api/workflows/workspaces/control/alice-workspace/workflows"
        self.headers = {"Authorization": "Bearer alice-token"}
        self.claims = {"email": "alice@example.com", "role": "member", "user_id": 7, "auth_provider": "local"}

    def test_browser_session_cannot_replace_workspace_token(self):
        # Arrange
        with patch.object(proxy, "proxy_http", AsyncMock()) as upstream:
            # Act
            response = self.client.post(self.base, json={})

        # Assert
        self.assertEqual(response.status_code, 401)
        upstream.assert_not_called()

    def test_submission_uses_authenticated_owner_and_shared_validation(self):
        # Arrange
        submitted = {"workflow": {"metadata": {"generateName": "train-"}, "spec": {"templates": []}}}
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims) as authenticate,
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "proxy_http", AsyncMock(return_value=Response(b"{}"))) as upstream,
        ):
            # Act
            response = self.client.post(self.base, headers=self.headers, json=submitted)

        # Assert
        self.assertEqual(response.status_code, 200)
        authenticate.assert_called_once_with("control", "alice-workspace", "alice-token")
        self.assertEqual(upstream.call_args.args[0], "api/v1/workflows/control")
        self.assertEqual(upstream.call_args.args[2], self.claims)

    def test_submission_injects_owner_identity_and_uses_backend_upstream_token(self):
        # Arrange
        submitted = {"workflow": {
            "metadata": {"generateName": "train-", "labels": {workspace_access.OWNER_LABEL: "8"}},
            "spec": {"entrypoint": "main", "templates": [{
                "name": "main", "container": {"image": "python:3.12"},
            }]},
        }}
        forwarded = []

        def handle(request):
            forwarded.append(request)
            return httpx.Response(200, json={"metadata": {"name": "train-123", "uid": "workflow-uid"}})

        original_client = httpx.AsyncClient
        transport = httpx.MockTransport(handle)
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "get_config", return_value=SimpleNamespace(
                enabled=True, server_url="http://argo", base_path="/api/workflows/proxy/",
            )),
            patch.object(proxy.auth, "authorization_headers", return_value={"authorization": "Bearer backend-token"}),
            patch.object(mlflow_delegation, "session_factory"),
            patch.object(mlflow_delegation, "require_user_entity", return_value=SimpleNamespace(id=7)),
            patch.object(mlflow_delegation.workflow_s3_credentials, "list_all", return_value=[]),
            patch.object(mlflow_delegation, "api_client"),
            patch.object(mlflow_delegation.client, "CoreV1Api") as core,
            patch.object(proxy.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(
                transport=transport, **kwargs,
            )),
        ):
            # Act
            response = self.client.post(self.base, headers=self.headers, json=submitted)

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(forwarded), 1)
        upstream = forwarded[0]
        self.assertEqual(upstream.headers["authorization"], "Bearer backend-token")
        workflow = json.loads(upstream.content)["workflow"]
        self.assertEqual(workflow["metadata"]["labels"][workspace_access.OWNER_LABEL], "7")
        self.assertEqual(workflow["metadata"]["annotations"][tracking.WORKFLOW_OWNER_ANNOTATION], "7")
        environment = {item["name"]: item for item in workflow["spec"]["templates"][0]["container"]["env"]}
        self.assertIn("/api/mlflow/tracking/workflows/control/", environment["MLFLOW_TRACKING_URI"]["value"])
        self.assertIn("secretKeyRef", environment["MLFLOW_TRACKING_TOKEN"]["valueFrom"])
        core.return_value.patch_namespaced_secret.assert_called_once()

    def test_workflow_list_adds_owner_filter_without_discarding_user_selectors(self):
        # Arrange
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "proxy_http", AsyncMock(return_value=Response(b"{}"))) as upstream,
        ):
            # Act
            response = self.client.get(self.base, headers=self.headers, params=[
                ("listOptions.labelSelector", "app=training"), ("listOptions.labelSelector", "team=ml"),
                ("listOptions.limit", "10"), ("workflow_name", "bobs-run"), ("action", "stop"),
            ])

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.call_args.args[0], "api/v1/workflows/control")
        query = upstream.call_args.args[1].query_params
        self.assertEqual(query["listOptions.labelSelector"],
                         f"app=training,team=ml,{workspace_access.OWNER_LABEL}=7")
        self.assertEqual(query["listOptions.limit"], "10")

    def test_empty_argo_list_with_null_items_is_accepted(self):
        # Arrange
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "proxy_http", AsyncMock(return_value=Response(b'{"items":null}'))),
        ):
            # Act
            response = self.client.get(self.base, headers=self.headers)

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_listing_checks_protected_owner_annotation_and_closes_upstream(self):
        # Arrange
        items = [{"metadata": {"name": name, "labels": {workspace_access.OWNER_LABEL: "7"}, "annotations": {
            tracking.WORKFLOW_OWNER_ANNOTATION: owner,
        }}} for name, owner in [("alices-run", "7"), ("forged-old-label", "8")]]

        async def chunks():
            yield json.dumps({"items": items}).encode()

        close_upstream = AsyncMock()
        stream = StreamingResponse(chunks(), background=BackgroundTask(close_upstream))
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "proxy_http", AsyncMock(return_value=stream)),
        ):
            # Act
            response = self.client.get(self.base, headers=self.headers)

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["metadata"]["name"] for item in response.json()["items"]], ["alices-run"])
        close_upstream.assert_awaited_once()

    def test_other_users_workflow_cannot_be_read_deleted_or_stopped(self):
        for method, suffix in [("GET", "/bobs-run"), ("DELETE", "/bobs-run"), ("PUT", "/bobs-run/stop")]:
            with self.subTest(method=method):
                # Arrange
                with (
                    patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
                    patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
                    patch.object(workspace_access, "api_client"),
                    patch.object(workspace_access.client, "CustomObjectsApi") as custom,
                    patch.object(proxy, "proxy_http", AsyncMock()) as upstream,
                ):
                    custom.return_value.get_namespaced_custom_object.return_value = {"metadata": {
                        "annotations": {tracking.WORKFLOW_OWNER_ANNOTATION: "8"},
                    }}

                    # Act
                    response = self.client.request(method, self.base + suffix, headers=self.headers)

                # Assert
                self.assertEqual(response.status_code, 403)
                upstream.assert_not_called()

    def test_owned_workflow_can_be_stopped(self):
        # Arrange
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(workspace_access, "api_client"),
            patch.object(workspace_access.client, "CustomObjectsApi") as custom,
            patch.object(proxy, "proxy_http", AsyncMock(return_value=Response(b"{}"))) as upstream,
        ):
            custom.return_value.get_namespaced_custom_object.return_value = {"metadata": {
                "annotations": {tracking.WORKFLOW_OWNER_ANNOTATION: "7"},
            }}

            # Act
            response = self.client.put(self.base + "/alices-run/stop", headers=self.headers, json={})

        # Assert
        self.assertEqual(response.status_code, 200)
        self.assertEqual(upstream.call_args.args[0], "api/v1/workflows/control/alices-run/stop")

    def test_retry_cannot_bypass_submission_validation(self):
        # Arrange
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(proxy, "proxy_http", AsyncMock()) as upstream,
        ):
            # Act
            response = self.client.put(self.base + "/alices-run/retry", headers=self.headers, json={})

        # Assert
        self.assertEqual(response.status_code, 403)
        upstream.assert_not_called()

    def test_gateway_secret_submission_is_rejected_for_workspace_credentials(self):
        # Arrange
        submitted = {"workflow": {"metadata": {"generateName": "attack-"}, "spec": {"templates": [{
            "name": "main", "container": {"image": "python:3.12", "env": [{
                "name": "STOLEN", "valueFrom": {"secretKeyRef": {
                    "name": "mlflow-tracking-gateway", "key": "gateway-token",
                }},
            }]},
        }]}}}
        with (
            patch.object(workspace_access, "authenticate_workspace", return_value=self.claims),
            patch.object(workspace_access, "get_config", return_value=SimpleNamespace(namespace="control")),
            patch.object(proxy, "get_config", return_value=SimpleNamespace(
                enabled=True, server_url="http://argo", base_path="/api/workflows/proxy/",
            )),
            patch.object(proxy.auth, "authorization_headers", return_value={"authorization": "Bearer backend-token"}),
            patch.object(mlflow_delegation, "session_factory"),
            patch.object(mlflow_delegation, "require_user_entity", return_value=SimpleNamespace(id=7)),
            patch.object(mlflow_delegation.workflow_s3_credentials, "list_all", return_value=[]),
            patch.object(proxy.httpx, "AsyncClient") as upstream,
        ):
            # Act
            response = self.client.post(self.base, headers=self.headers, json=submitted)

        # Assert
        self.assertEqual(response.status_code, 403)
        upstream.assert_not_called()

    def test_workspace_credential_cannot_use_browser_proxy_or_admin_migration(self):
        # Arrange
        self.client.app.dependency_overrides.clear()
        with patch.object(proxy, "proxy_http", AsyncMock()) as upstream, patch.object(
            workspace_access, "upgrade_workspaces",
        ) as migrate, patch.object(
            token_extractor.auth, "verify_token", AsyncMock(side_effect=ValueError("Invalid token")),
        ):
            # Act
            proxied = self.client.post(
                "/api/workflows/proxy/api/v1/workflows/control", headers=self.headers, json={},
            )
            migration = self.client.post("/api/workflows/upgrade-workspaces", headers=self.headers)

        # Assert
        self.assertEqual(proxied.status_code, 401)
        self.assertEqual(migration.status_code, 401)
        upstream.assert_not_called()
        migrate.assert_not_called()

    def test_migration_is_admin_only(self):
        # Arrange
        self.client.app.dependency_overrides[get_claims] = lambda: {"role": "member"}
        with patch.object(workspace_access, "upgrade_workspaces") as migrate:
            # Act
            response = self.client.post("/api/workflows/upgrade-workspaces")

        # Assert
        self.assertEqual(response.status_code, 403)
        migrate.assert_not_called()


class WorkspaceArgoProvisioningTests(unittest.TestCase):
    def test_new_workspace_has_separate_argo_and_mlflow_credentials(self):
        # Arrange
        namespace, name, secret_name = "control", "alice-workspace", "workspace-secret"

        # Act
        secret = workspace_k8s._secret_manifest(namespace, secret_name)
        environment = workspace_access.environment(namespace, name, secret_name)

        # Assert
        self.assertIn(workspace_access.TOKEN_KEY, secret["stringData"])
        self.assertNotEqual(secret["stringData"][workspace_access.TOKEN_KEY],
                            secret["stringData"][tracking.TRACKING_TOKEN_KEY])
        self.assertEqual(environment[1]["valueFrom"]["secretKeyRef"]["name"], secret_name)
        self.assertTrue(environment[0]["value"].endswith(f"/workspaces/{namespace}/{name}/workflows"))

    def test_migration_preserves_storage_and_other_credentials(self):
        # Arrange
        with (
            patch.object(workspace_k8s, "api_client"),
            patch("kubernetes.client.CoreV1Api") as core,
            patch("kubernetes.client.AppsV1Api") as apps,
        ):
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={
                "S3_PROFILE_TOKEN": "keep-s3", tracking.TRACKING_TOKEN_KEY: "keep-mlflow",
            })

            # Act
            workspace_k8s.enable_argo_access("control", "alice-workspace", "workspace-secret")

        # Assert
        patch_body = core.return_value.patch_namespaced_secret.call_args.args[2]
        self.assertEqual(set(patch_body["stringData"]), {workspace_access.TOKEN_KEY})
        workload_patch = apps.return_value.patch_namespaced_stateful_set.call_args.args[2]
        self.assertNotIn("volumeClaimTemplates", workload_patch["spec"])
        environment = workload_patch["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(environment[0]["name"], "TRITON_CONTROL_ARGO_URL")
        self.assertIsNone(environment[0]["valueFrom"])
        self.assertIsNone(environment[1]["value"])

    def test_migration_does_not_rotate_existing_argo_token(self):
        # Arrange
        with (
            patch.object(workspace_k8s, "api_client"),
            patch("kubernetes.client.CoreV1Api") as core,
            patch("kubernetes.client.AppsV1Api"),
        ):
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={
                workspace_access.TOKEN_KEY: "keep-argo",
            })

            # Act
            workspace_k8s.enable_argo_access("control", "alice-workspace", "workspace-secret")

        # Assert
        core.return_value.patch_namespaced_secret.assert_not_called()
