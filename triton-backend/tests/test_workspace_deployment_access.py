"""Workspace deployment credentials remain owner-bound and API-restricted."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.middleware.sessions import SessionMiddleware

from app.core import security
from app.db.entities import CodeServerEntity, UserEntity
from app.exceptions import ForbiddenError, UnauthorizedError
from app.services.development import deployment_access


class WorkspaceDeploymentAccessTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member", is_active=True, credential_version=3))
            session.add(CodeServerEntity(owner_user_id=7, name="workspace", namespace="control",
                statefulset_name="code-7", service_name="code-7-svc", secret_name="code-7-secret", image="python:3.12"))
            session.commit()
        self.token = deployment_access.create_token("control", "code-7")
        self.secret = SimpleNamespace(data={deployment_access.TOKEN_KEY: base64.b64encode(self.token.encode()).decode()})
        patches = [
            patch.object(deployment_access, "session_factory", side_effect=lambda: Session(self.engine)),
            patch.object(security, "session_factory", side_effect=lambda: Session(self.engine)),
            patch.object(deployment_access, "api_client"),
            patch.object(deployment_access.client, "CoreV1Api"),
        ]
        for mocked in patches:
            result = mocked.start()
            self.addCleanup(mocked.stop)
        self.core = result.return_value
        self.core.read_namespaced_secret.return_value = self.secret

    def test_authenticates_current_database_owner_and_checks_credential(self):
        claims = deployment_access.authenticate_token(self.token)
        self.assertEqual(claims["user_id"], 7)
        self.assertEqual(claims["token_use"], "workspace_deployment")
        self.assertEqual(claims["credential_version"], 3)
        self.core.read_namespaced_secret.assert_called_once_with("code-7-secret", "control")

    def test_wrong_or_malformed_credentials_are_rejected(self):
        for token in ["", "workspace-deployment:", self.token + "wrong", self.token.replace(":code-7:", ":code-8:"), "workspace-deployment:control:code-7:" + "x" * 513]:
            with self.subTest(token=token[:50]), self.assertRaises(UnauthorizedError):
                deployment_access.authenticate_token(token)
        for stored in ["", "not-base64!"]:
            self.secret.data[deployment_access.TOKEN_KEY] = stored
            with self.assertRaises(UnauthorizedError):
                deployment_access.authenticate_token(self.token)

    def test_deleted_workspace_or_secret_revokes_token(self):
        self.core.read_namespaced_secret.side_effect = ApiException(status=404)
        with self.assertRaises(UnauthorizedError):
            deployment_access.authenticate_token(self.token)
        self.core.read_namespaced_secret.side_effect = None
        with Session(self.engine) as session:
            from app.repositories import developments
            session.delete(developments.find_by_workload(session, "control", "code-7"))
            session.commit()
        with self.assertRaises(UnauthorizedError):
            deployment_access.authenticate_token(self.token)

    def test_disabled_owner_or_removed_role_revokes_token(self):
        for role, active in [("member", False), ("viewer", True)]:
            with Session(self.engine) as session:
                owner = session.get(UserEntity, 7)
                owner.role, owner.is_active = role, active
                session.add(owner)
                session.commit()
            with self.subTest(role=role, active=active), self.assertRaises(ForbiddenError):
                deployment_access.authenticate_token(self.token)

    def test_bearer_token_allows_only_plugin_apis(self):
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key="test-session")

        async def identity(claims=Depends(security.get_claims)):
            return {"owner": claims["user_id"]}

        for path, methods in [
            ("/api/deployments", ["POST"]),
            ("/api/deployments/mlflow", ["GET"]),
            ("/api/deployments/mlflow/iris", ["GET", "DELETE"]),
            ("/api/s3-profiles/resolve", ["GET"]),
            ("/api/s3-profiles", ["GET", "POST"]),
            ("/api/users", ["GET"]),
            ("/api/deployments/1", ["DELETE"]),
        ]:
            app.add_api_route(path, identity, methods=methods)
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + self.token}
            for method, path in [("POST", "/api/deployments"), ("GET", "/api/deployments/mlflow"), ("DELETE", "/api/deployments/mlflow/iris"), ("GET", "/api/s3-profiles/resolve")]:
                response = client.request(method, path, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"owner": 7})
            for method, path in [("GET", "/api/s3-profiles"), ("POST", "/api/s3-profiles"), ("GET", "/api/users"), ("DELETE", "/api/deployments/1")]:
                self.assertEqual(client.request(method, path, headers=headers).status_code, 403)
            self.secret.data.clear()
            self.assertEqual(client.get("/api/deployments/mlflow", headers=headers).status_code, 401)
