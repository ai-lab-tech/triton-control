"""Name-based deployment profile references and workflow token isolation."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import s3_profile_api
from app.core import security
from app.db.database import get_session
from app.db.entities import S3ProfileEntity, UserEntity


class ProfileResolutionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member", is_active=True))
            session.add(UserEntity(id=8, email="other@example.com", name="Other", role="member", is_active=True))
            session.add(UserEntity(id=9, email="admin@example.com", name="Admin", role="admin", is_active=True))
            for profile_id, owner, name in [(11, 7, "training"), (12, 7, "other-owned"), (13, 8, "foreign")]:
                session.add(
                    S3ProfileEntity(
                        id=profile_id,
                        owner_user_id=owner,
                        name=name,
                        endpoint="https://s3.test",
                        bucket="models",
                        access_key="private-access",
                        secret_key_enc="private-secret",
                    )
                )
            session.commit()
        self.claims = {"email": "owner@example.com", "auth_provider": "local", "role": "member"}
        app = FastAPI()
        app.include_router(s3_profile_api.router)

        def session_dependency():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_dependency
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def request(self, path, claims=None, **kwargs):
        claims = self.claims if claims is None else claims
        with (
            patch.object(security, "extract_claims", AsyncMock(return_value=claims)),
            patch.object(
                security,
                "_resolve_access_claims",
                return_value=claims,
            ),
        ):
            return self.client.get(path, headers={"Authorization": "Bearer test"}, **kwargs)

    def test_user_resolves_owned_name_without_receiving_credentials(self):
        response = self.request("/api/s3-profiles/resolve", params={"name": "training"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": 11, "name": "training"})
        self.assertNotIn("private", response.text)

    def add_shared_profile(self, name="shared-training"):
        with Session(self.engine) as session:
            session.add(
                S3ProfileEntity(
                    id=14,
                    owner_user_id=9,
                    name=name,
                    endpoint="https://s3.test",
                    bucket="models",
                    access_key="private-access",
                    secret_key_enc="private-secret",
                )
            )
            session.commit()

    def test_member_and_deployment_tokens_resolve_shared_profile_without_credentials(self):
        self.add_shared_profile()
        for token_use in [None, "workflow_deployment", "workspace_deployment"]:
            with self.subTest(token_use=token_use):
                claims = {**self.claims, "token_use": token_use}
                response = self.request("/api/s3-profiles/resolve", claims, params={"name": "shared-training"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"id": 14, "name": "shared-training"})
                self.assertNotIn("private", response.text)

    def test_member_resolves_another_members_legacy_profile(self):
        claims = self.claims
        response = self.request("/api/s3-profiles/resolve", claims, params={"name": "foreign"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"id": 13, "name": "foreign"})

    def test_database_rejects_duplicate_names_from_another_creator(self):
        # Arrange: training already belongs to another creator.
        # Act
        with self.assertRaises(IntegrityError):
            self.add_shared_profile(name="training")
        # Assert
        response = self.request("/api/s3-profiles/resolve", params={"name": "training"})
        self.assertEqual(response.json(), {"id": 11, "name": "training"})

    def test_scoped_workflow_cannot_resolve_unapproved_shared_profile(self):
        self.add_shared_profile()
        claims = {**self.claims, "token_use": "mlflow_workflow", "allowed_s3_profile_id": 11}
        response = self.request("/api/s3-profiles/resolve", claims, params={"name": "shared-training"})
        self.assertEqual(response.status_code, 403)

    def test_unknown_case_mismatched_or_empty_name_is_rejected(self):
        for name in ["unknown", "Training", ""]:
            with self.subTest(name=name):
                response = self.request("/api/s3-profiles/resolve", params={"name": name})
                self.assertIn(response.status_code, [400, 404])

    def test_workflow_can_resolve_only_its_allowed_profile(self):
        claims = {**self.claims, "token_use": "mlflow_workflow", "allowed_s3_profile_id": 11}
        allowed = self.request("/api/s3-profiles/resolve", claims, params={"name": "training"})
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json(), {"id": 11, "name": "training"})
        forbidden = self.request("/api/s3-profiles/resolve", claims, params={"name": "other-owned"})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(self.request("/api/s3-profiles", claims).status_code, 403)

    def test_workflow_missing_scope_cannot_resolve_profile(self):
        response = self.request(
            "/api/s3-profiles/resolve", {**self.claims, "token_use": "mlflow_workflow"}, params={"name": "training"}
        )
        self.assertEqual(response.status_code, 403)

    def test_automatic_workflow_token_resolves_global_profiles(self):
        claims = {**self.claims, "token_use": "workflow_deployment"}
        for name in ["training", "other-owned"]:
            self.assertEqual(self.request("/api/s3-profiles/resolve", claims, params={"name": name}).status_code, 200)
        self.assertEqual(self.request("/api/s3-profiles/resolve", claims, params={"name": "foreign"}).status_code, 200)
        self.assertEqual(self.request("/api/s3-profiles", claims).status_code, 403)
