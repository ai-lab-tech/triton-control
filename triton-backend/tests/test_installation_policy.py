"""AAA tests: startup, role ceilings, direct use cases, API aliases and proxy denial."""

import asyncio
import os
import re
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.installation_policy import PRODUCTION_ENDPOINTS, InstallationPolicyMiddleware
from app.core import installation_policy as policy
from app.core.security import get_claims
from app.db.database import get_session
from app.exceptions import ForbiddenError
from app.main import app
from app.services import operations
from app.services.builds import execution
from app.schemas.model_state import ModelState
from app.services.deployment import model_state
from app.services.development import deployment_access, workspaces
from app.services.mlflow import installer, migration
from app.services.storage import s3
from app.services.triton import instances, models
from app.services.workflows import proxy


class ModeTests(unittest.TestCase):
    def tearDown(self):
        policy.mode.cache_clear()

    def test_omitted_mode_defaults_to_development_and_is_fixed_for_process(self):
        # Arrange
        policy.mode.cache_clear()
        with patch.dict(os.environ, {}, clear=True):
            # Act
            first = policy.mode()
            os.environ["TRITON_CONTROL_MODE"] = "production"
            second = policy.mode()
        # Assert
        self.assertEqual((first, second), ("development", "development"))

    def test_invalid_mode_fails_before_database_or_worker_startup(self):
        # Arrange
        from app.main import on_startup
        for value in ("", "Production", "production ", "staging", "true"):
            policy.mode.cache_clear()
            with patch.dict(os.environ, {"TRITON_CONTROL_MODE": value}), patch("app.main.init_db") as db:
                # Act / Assert
                with self.subTest(value=value), self.assertRaisesRegex(RuntimeError, "TRITON_CONTROL_MODE"):
                    asyncio.run(on_startup())
                db.assert_not_called()

    def test_role_mode_capabilities_are_ceilings_without_approval_bypass(self):
        # Arrange / Act / Assert
        for mode in ("development", "production"):
            with patch.object(policy, "mode", return_value=mode):
                for role in ("viewer", "member", "admin"):
                    result = policy.capabilities(role).capabilities
                    self.assertTrue(result["serving_read"])
                    self.assertTrue(result["artifact_read"])
                    self.assertFalse(result["production_changes"])
                    self.assertEqual(result["identity_admin"], role == "admin")
                    self.assertEqual(result["interactive_inference"], mode == "development")
                    self.assertEqual(result["direct_deployment"], mode == "development" and role != "viewer")
        with patch.object(policy, "mode", return_value="production"):
            self.assertFalse(any(policy.capabilities("pending").capabilities.values()))


class ProductionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.object(policy, "mode", return_value="production")
        self.mode.start()
        self.addCleanup(self.mode.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(app.dependency_overrides.clear)
        self.session = MagicMock()
        app.dependency_overrides[get_session] = lambda: self.session

    def claims(self, role):
        app.dependency_overrides[get_claims] = lambda: {"role": role, "email": role + "@test", "access_allowed": True}

    def test_every_nonallowlisted_http_route_and_alias_denied_for_all_roles(self):
        # Arrange: enumerate real route templates so additions cannot silently escape the policy.
        routes = [r for r in app.routes if getattr(r, "methods", None)]
        for role in ("viewer", "member", "admin"):
            self.claims(role)
            for route in routes:
                identity = route.endpoint.__module__ + "." + route.endpoint.__name__
                if identity in PRODUCTION_ENDPOINTS:
                    continue
                path = re.sub(r"\{[^}]+\}", "1", route.path)
                for method in route.methods:
                    if method == "OPTIONS":
                        continue
                    # Act
                    response = self.client.request(method, path, json={})
                    # Assert
                    with self.subTest(role=role, method=method, path=path):
                        self.assertEqual(response.status_code, 403)
        self.session.execute.assert_not_called()
        self.session.commit.assert_not_called()

    def test_capabilities_and_authorized_serving_reads_remain_available(self):
        # Arrange
        for role in ("viewer", "member", "admin"):
            self.claims(role)
            with patch.object(models, "list_models", new=AsyncMock(return_value=[])) as read:
                # Act
                caps = self.client.get("/api/capabilities")
                response = self.client.get("/api/instances/1/models")
                # Assert
                self.assertEqual(caps.status_code, 200)
                self.assertEqual(caps.json()["mode"], "production")
                self.assertFalse(caps.json()["capabilities"]["interactive_inference"])
                self.assertEqual(response.status_code, 200)
                read.assert_awaited_once()
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_production_exposes_read_only_model_state(self):
        self.claims("member")
        state = ModelState(
            capacity_owner="source", mode="declared", replicas=1, rollout_complete=True,
            interactive_allowed=False, can_declare=False, can_restore=False, can_scale_above_one=False,
        )
        with patch.object(model_state, "get_state", return_value=state):
            response = self.client.get("/api/instances/1/model-state")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["capacity_owner"], "source")
        self.assertEqual(self.client.post("/api/instances/1/model-state/scale", json={"replicas": 2}).status_code, 403)

    def test_instance_credential_rotation_reaches_scope_checked_service(self):
        from app.services.storage import s3 as storage
        self.claims("admin")
        with patch.object(storage, "update_instance_s3", side_effect=ForbiddenError("Scope differs")) as rotate:
            response = self.client.put("/api/instances/1/s3", json={"enabled": True, "s3_profile_id": 1})
        rotate.assert_called_once()
        self.assertEqual(response.status_code, 403)
        self.assertIn("Scope differs", response.text)

    def test_existing_workspaces_and_argo_websocket_aliases_are_closed(self):
        # Arrange / Act / Assert
        for path in ("/api/development/1/proxy", "/api/development/1/proxy/socket", "/api/workflows/proxy",
                     "/api/workflows/proxy/socket"):
            with self.subTest(path=path), self.assertRaises(WebSocketDisconnect) as raised:
                with self.client.websocket_connect(path):
                    self.fail("Production must not accept a development WebSocket")
            self.assertEqual(raised.exception.code, 1008)

    def test_unclassified_get_execution_is_denied_not_assumed_read_only(self):
        # Arrange
        application = FastAPI()
        side_effect = MagicMock()

        @application.get("/api/new-executor")
        def unreviewed():
            side_effect()
            return {"started": True}

        application.add_middleware(InstallationPolicyMiddleware, routes=application.router.routes)
        # Act
        with TestClient(application) as client:
            response = client.get("/api/new-executor")
        # Assert
        self.assertEqual(response.status_code, 403)
        side_effect.assert_not_called()

    def test_admin_direct_services_and_background_calls_fail_before_io(self):
        # Arrange
        session = MagicMock()
        claims = {"role": "admin", "email": "admin@test"}
        calls = [lambda: instances.delete_instance(session, claims, 1),
                 lambda: workspaces.create_code_server(None, session, claims),
                 lambda: installer.install_mlflow(None, session, claims),
                 lambda: deployment_access.authenticate_token("old-workspace-token"),
                 lambda: migration.reconcile(),
                 lambda: execution.submit(session, claims, None, "key"),
                 lambda: s3.delete_instance_s3_content(session, claims, 1, "model/config.pbtxt")]
        for call in calls:
            # Act / Assert
            with self.assertRaises(ForbiddenError):
                call()
        session.commit.assert_not_called()
        session.exec.assert_not_called()
        worker = operations.OperationWorker(operations.OperationService(), lambda: session)
        self.assertFalse(worker.run_once())

    def test_async_inference_and_proxies_deny_direct_non_http_callers(self):
        # Arrange / Act / Assert
        async def check():
            for call in (lambda: models.infer_model(None, {}, 1, "model", "1", b"{}", "application/json"),
                         lambda: proxy.proxy_http("api/v1/workflows", None, {"role": "admin"})):
                with self.assertRaises(ForbiddenError):
                    await call()
        asyncio.run(check())

    def test_delegated_tokens_denied_before_identity_resolution(self):
        # Arrange
        from app.core import security
        request = MagicMock()
        request.url.path, request.method = "/api/deployments", "POST"
        for use in ("workspace_deployment", "workflow_deployment", "mlflow_workflow"):
            with patch.object(security, "extract_claims", new=AsyncMock(return_value={"token_use": use})), \
                 patch.object(security, "_resolve_access_claims") as resolve:
                # Act / Assert
                with self.assertRaises(HTTPException) as raised:
                    asyncio.run(security._get_claims_with_access_policy(request, None))
                self.assertEqual(raised.exception.status_code, 403)
                resolve.assert_not_called()
