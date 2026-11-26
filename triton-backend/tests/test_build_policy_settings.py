"""Exercise admin policy changes through the settings API and runtime resolver."""

import os
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.crypto import encrypt_secret
from app.api.build_settings_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import S3ProfileEntity, UserEntity
from app.schemas.build_policy import LocalArchivePolicy, ManagedRegistryPolicy
from app.services.registry_settings import encrypt_registry_secret
from app.repositories import build_config, registry_config
from app.services.builds.contexts_storage import binding
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
                                        bucket="contexts", access_key="fixture", secret_key_enc=encrypt_secret("fixture"), prefix=""))
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

    def test_destination_can_be_saved_before_cluster_setup(self):
        url = self.url + "/destination"
        destination = {"namespace": "triton-builds", "allowed_repositories": ["registry.test/team/image"]}

        response = self.client.put(url, json=destination)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get(url).json()["destination"], {**destination, "push_secret": None})
        self.assertIn("needs setup", response.json()["status"])
        self.assertIsNone(self.client.get(self.url).json()["policy"])

    def test_destination_overrides_only_runtime_target_and_reset_restores_it(self):
        candidate = policy()
        self.client.put(self.url, json=candidate.model_dump(mode="json")).raise_for_status()
        url = self.url + "/destination"
        destination = {"namespace": "other-builds", "push_secret": "writer", "allowed_repositories": ["registry.test/team/image"]}

        self.client.put(url, json=destination).raise_for_status()

        with Session(self.engine) as session:
            result = runtime_policy(session)
            self.assertEqual(result.push_secret, "writer")
            self.assertEqual(result.runtime_pull_secret, candidate.runtime_pull_secret)
            self.assertEqual(result.namespace, destination["namespace"])
            self.assertEqual(list(result.allowed_repositories), destination["allowed_repositories"])
            self.assertEqual(result.s3_profile_name, candidate.s3_profile_name)
            self.assertNotEqual(result.fingerprint(), candidate.fingerprint())
        self.client.delete(url).raise_for_status()
        with Session(self.engine) as session:
            self.assertEqual(runtime_policy(session), candidate)

    def test_empty_destination_is_local_and_never_falls_back_to_remote(self):
        url = self.url + "/destination"
        candidate = policy().model_dump(mode="json")
        self.client.put(self.url, json=candidate).raise_for_status()
        local = {"namespace": "builds", "allowed_repositories": []}

        response = self.client.put(url, json=local)

        response.raise_for_status()
        self.assertEqual(response.json()["destination"]["allowed_repositories"], [])
        with Session(self.engine) as session:
            self.assertIsInstance(runtime_policy(session), LocalArchivePolicy)

    def test_credentials_are_write_only_and_survive_destination_only_edits(self):
        self.enterContext(patch.dict(os.environ, REGISTRY_SECRET_ENCRYPTION_KEY="unit-test"))
        url = self.url + "/destination"
        local = {"namespace": "builds", "allowed_repositories": []}
        credentials = '{"auths":{"registry.test":{"auth":"fixture-private-token"}}}'

        saved = self.client.put(url, json={**local, "dockerconfigjson": credentials})

        saved.raise_for_status()
        self.assertTrue(saved.json()["push_credentials_saved"])
        self.assertNotIn("fixture-private-token", saved.text)
        self.assertTrue(self.client.put(url, json=local).json()["push_credentials_saved"])
        with Session(self.engine) as session:
            row = build_config.get_destination(session)
            self.assertIsNotNone(row)
            self.assertNotIn("fixture-private-token", row.push_config_enc)
        self.assertFalse(self.client.put(url, json={**local, "dockerconfigjson": ""}).json()["push_credentials_saved"])

    def test_reuse_tracks_saved_pull_credentials_and_unchecking_selects_anonymous(self):
        self.enterContext(patch.dict(os.environ, REGISTRY_SECRET_ENCRYPTION_KEY="unit-test"))
        candidate = policy()
        self.client.put(self.url, json=candidate.model_dump(mode="json")).raise_for_status()
        url = self.url + "/destination"
        destination = {"namespace": candidate.namespace, "allowed_repositories": candidate.allowed_repositories}
        reuse = {**destination, "reuse_pull_credentials": True}
        self.assertEqual(self.client.put(url, json=reuse).status_code, 403)
        with Session(self.engine) as session:
            registry_config.save(session, encrypt_registry_secret('{"auths":{"registry.test":{"auth":"fixture"}}}'))

        saved = self.client.put(url, json=reuse)
        saved.raise_for_status()

        self.assertTrue(self.client.get(url).json()["reuse_pull_credentials"])
        self.assertFalse(saved.json()["push_credentials_saved"])
        with Session(self.engine) as session:
            original = runtime_policy(session)
            self.assertFalse(original.anonymous_push)
            registry_config.save(session, encrypt_registry_secret('{"auths":{"registry.test":{"auth":"changed"}}}'))
            self.assertNotEqual(original.fingerprint(), runtime_policy(session).fingerprint())
        self.client.put(url, json={**destination, "reuse_pull_credentials": False}).raise_for_status()
        with Session(self.engine) as session:
            self.assertTrue(runtime_policy(session).anonymous_push)

    def test_default_executor_matches_the_published_release_and_configured_overrides_are_reported(self):
        candidate = policy().model_dump(mode="json")
        default = self.client.get(self.url + "/destination").json()["builder"]

        without_builder = {key: value for key, value in candidate.items() if key not in {"executor_image", "release"}}
        self.client.put(self.url, json=without_builder).raise_for_status()

        self.assertEqual(default["release"], "v1.29.1")
        self.assertTrue(default["executor_image"].startswith("ghcr.io/osscontainertools/kaniko@sha256:"))
        with Session(self.engine) as session:
            self.assertEqual(runtime_policy(session).executor_image, default["executor_image"])
        self.client.put(self.url, json=candidate).raise_for_status()
        self.assertEqual(self.client.get(self.url + "/destination").json()["builder"]["executor_image"],
                         candidate["executor_image"])

    def test_helm_builder_is_used_by_admin_policy_and_advanced_view(self):
        candidate = policy().model_dump(mode="json", exclude={"executor_image", "release"})
        builder = '{"release":"v2.0.1","executor_image":"mirror.test/kaniko@sha256:' + "1" * 64 + '"}'

        with patch.dict(os.environ, TRITON_BUILD_DEFAULT_EXECUTOR=builder):
            self.assertEqual(self.client.get(self.url + "/destination").json()["builder"]["release"], "v2.0.1")
            self.client.put(self.url, json=candidate).raise_for_status()
            with Session(self.engine) as session:
                self.assertEqual(runtime_policy(session).release, "v2.0.1")
        with Session(self.engine) as session:
            self.assertEqual(runtime_policy(session).release, "v1.29.1")

    def test_development_local_archive_uses_single_profile_and_managed_namespace_without_receipt(self):
        from app.services.builds.execution import qualified

        with patch.dict(os.environ, TRITON_LOCAL_BUILD_NAMESPACE="triton-builds",
                        TRITON_BUILD_FIXTURE_REVISION="", TRITON_CONTROL_MODE="development"):
            with Session(self.engine) as session:
                settings = qualified(session)

            self.assertIsInstance(settings, LocalArchivePolicy)
            self.assertEqual(settings.namespace, "triton-builds")
            with Session(self.engine) as session:
                self.assertEqual(binding(session, settings)["bucket"], "contexts")
            self.assertEqual(settings.allowed_repositories, ("local/serving",))
            self.client.put(self.url + "/destination", json={
                "namespace": "triton-builds", "allowed_repositories": ["remote.test/models"],
            }).raise_for_status()
            with Session(self.engine) as session:
                remote = qualified(session)
                self.assertIsInstance(remote, ManagedRegistryPolicy)
                self.assertEqual(remote.allowed_repositories, ("remote.test/models",))

    def test_profile_created_after_policy_load_can_be_selected_for_staging(self):
        with patch.dict(os.environ, TRITON_LOCAL_BUILD_NAMESPACE="triton-builds"), Session(self.engine) as session:
            settings = runtime_policy(session)
            session.add(S3ProfileEntity(owner_user_id=1, name="later", endpoint="https://storage.test",
                                        bucket="new-bucket", prefix="team", access_key="fixture",
                                        secret_key_enc=encrypt_secret("fixture")))
            session.commit()

            selected = binding(session, settings, "later")

            self.assertEqual(selected["bucket"], "new-bucket")
            self.assertEqual(selected["prefix"], "team/build-contexts/")
            self.assertEqual(runtime_policy(session).fingerprint(), settings.fingerprint())

    def test_blank_override_and_reset_restore_helm_destination(self):
        url = self.url + "/destination"
        with patch.dict(os.environ, TRITON_LOCAL_BUILD_NAMESPACE="triton-builds",
                        TRITON_BUILD_DESTINATION_REPOSITORIES='["registry.test/team/model"]'):
            self.assertEqual(self.client.get(url).json()["destination"]["allowed_repositories"],
                             ["registry.test/team/model"])

            self.client.put(url, json={"namespace": "triton-builds", "allowed_repositories": []}).raise_for_status()
            with Session(self.engine) as session:
                self.assertIsInstance(runtime_policy(session), LocalArchivePolicy)

            self.client.delete(url).raise_for_status()
            with Session(self.engine) as session:
                restored = runtime_policy(session)
                self.assertIsInstance(restored, ManagedRegistryPolicy)
                self.assertTrue(restored.permits_destination("registry.test/team/model:v1"))
