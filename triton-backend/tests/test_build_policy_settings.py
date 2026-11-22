"""Exercise admin policy changes through the settings API and runtime resolver."""

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.build_settings_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import S3ProfileEntity, UserEntity
from app.services.builds.contexts_storage import policy as runtime_policy
from tests.test_build_contexts import policy


class BuildSettingsTests(unittest.TestCase):
    url = "/api/settings/serving-image-builds"

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            session.add(UserEntity(id=1, email="admin@test", name="Admin", role="admin"))
            session.add(UserEntity(id=2, email="user@test", name="Engineer", role="member"))
            session.commit()
            session.add(S3ProfileEntity(owner_user_id=1, name="staging", endpoint="https://storage.test",
                                        bucket="contexts", access_key="fixture", secret_key_enc="fixture", prefix=""))
            session.commit()
        self.claims = {"email": "admin@test", "sub": "admin@test"}

        def session_dependency():
            with Session(self.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = session_dependency
        app.dependency_overrides[get_claims] = lambda: self.claims
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.enterContext(patch.dict(os.environ, TRITON_BUILD_POLICY_FILE="", TRITON_BUILD_FIXTURE_REVISION="test"))

    def test_save_applies_to_runtime_and_reset_restores_unconfigured_state(self):
        candidate = policy()
        response = self.client.put(self.url, json=candidate.model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(self.url).json()["source"], "admin")
        with Session(self.engine) as session:
            self.assertEqual(runtime_policy(session), candidate)
        self.assertEqual(self.client.delete(self.url).json(), {"source": "unconfigured", "policy": None})

    def test_enabling_without_matching_qualification_preserves_saved_policy(self):
        draft = policy().model_dump(mode="json")
        self.assertEqual(self.client.put(self.url, json=draft).status_code, 200)
        response = self.client.put(self.url, json={**draft, "enabled": True})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.client.get(self.url).json()["policy"]["enabled"])

    def test_member_and_delegated_admin_cannot_manage_settings(self):
        for claims in ({"email": "user@test", "sub": "user@test"}, {**self.claims, "token_use": "deployment"}):
            self.claims = claims
            for response in (self.client.get(self.url), self.client.put(self.url, json=policy().model_dump(mode="json")),
                             self.client.delete(self.url)):
                self.assertEqual(response.status_code, 403)
