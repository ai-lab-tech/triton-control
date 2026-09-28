"""Workspace-only profile authentication and current owner isolation."""

import base64
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.development_api import router
from app.db.database import get_session
from app.db.entities import CodeServerEntity, S3ProfileEntity, UserEntity
from app.exceptions import ForbiddenError, UnauthorizedError
from app.services.development.profiles import workspace_profiles


class WorkspaceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        for owner in (1, 2):
            self.session.add(UserEntity(id=owner, email=f"user{owner}@example.com", name="User",
                                        role="member", is_active=True))
            self.session.add(CodeServerEntity(id=owner, owner_user_id=owner, name="workspace", namespace="dev",
                statefulset_name=f"code-{owner}", service_name=f"code-{owner}-svc",
                secret_name=f"code-{owner}-secret", image="test"))
            self.session.add(S3ProfileEntity(id=owner, owner_user_id=owner, name=f"profile-{owner}",
                endpoint="https://s3.example.com", bucket="models", access_key="key", secret_key_enc="secret"))
        self.session.commit()
        patch("app.services.development.profiles.api_client").start()
        self.core = patch("app.services.development.profiles.client.CoreV1Api").start().return_value
        self.core.read_namespaced_secret.side_effect = lambda name, namespace: SimpleNamespace(
            data={"S3_PROFILE_TOKEN": base64.b64encode(f"token-{name}".encode()).decode()},
        )
        self.addCleanup(patch.stopall)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

    def test_OwnedProfilesReflectEditsAndDeletion(self) -> None:
        profiles = workspace_profiles(self.session, "dev", "code-1", "token-code-1-secret")
        self.assertEqual([p.id for p in profiles], [1])
        row = self.session.get(S3ProfileEntity, 1)
        assert row
        row.ca_certificate = "updated-ca"
        self.session.add(row)
        self.session.commit()
        self.assertEqual(workspace_profiles(self.session, "dev", "code-1", "token-code-1-secret")[0].ca_certificate,
                         "updated-ca")
        self.session.delete(row)
        self.session.commit()
        self.assertEqual(workspace_profiles(self.session, "dev", "code-1", "token-code-1-secret"), [])

    def test_TokenCannotAccessAnotherWorkspaceOrNamespace(self) -> None:
        for namespace, name, token in [("dev", "code-2", "token-code-1-secret"),
                                        ("other", "code-1", "token-code-1-secret"),
                                        ("dev", "code-1", ""), ("dev", "code-1", "wrong")]:
            with self.subTest(namespace=namespace, name=name, token=token), self.assertRaises(UnauthorizedError):
                workspace_profiles(self.session, namespace, name, token)

    def test_InactiveAndViewerOwnersAreDenied(self) -> None:
        user = self.session.get(UserEntity, 1)
        assert user
        for active, role in [(False, "member"), (True, "viewer")]:
            user.is_active = active
            user.role = role
            self.session.add(user)
            self.session.commit()
            with self.assertRaises(ForbiddenError):
                workspace_profiles(self.session, "dev", "code-1", "token-code-1-secret")

    def test_DeletedWorkspaceRevokesToken(self) -> None:
        self.session.delete(self.session.get(CodeServerEntity, 1))
        self.session.commit()
        with self.assertRaises(UnauthorizedError):
            workspace_profiles(self.session, "dev", "code-1", "token-code-1-secret")

    def test_HttpEndpointRequiresWorkspaceTokenAndDisablesCaching(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        with TestClient(app) as client:
            url = "/api/development/workspace-s3-profiles/dev/code-1"
            self.assertEqual(client.get(url).status_code, 401)
            response = client.get(url, headers={"Authorization": "Bearer token-code-1-secret"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual([p["id"] for p in response.json()], [1])
            self.assertEqual(client.get(url.replace("code-1", "code-2"), headers={
                "Authorization": "Bearer token-code-1-secret",
            }).status_code, 401)
