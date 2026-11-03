"""Central credential authorization, redaction and workload resolution."""

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.registry_api import RegistryUpdate, get_registry_settings, put_registry_settings
from app.db.entities import S3ProfileEntity, UserEntity
from app.exceptions import BadRequestError, ForbiddenError
from app.schemas import CreateDeploymentRequest, CreateS3ProfileRequest, UpdateS3ProfileRequest
from app.schemas.development import CreateCodeServerRequest
from app.schemas.mlflow import InstallMlflowRequest
from app.schemas.perf_jobs import StartModelPerfRequest
from app.services.deployment.profile_request import resolve_profile_request
from app.services.registry_settings import apply_registry_settings
from app.services.storage import s3_profiles


class CentralSettingsTests(unittest.TestCase):
    def setUp(self):
        key = patch.dict("os.environ", {"REGISTRY_SECRET_ENCRYPTION_KEY": "test-key"})
        key.start()
        self.addCleanup(key.stop)
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.admin = {"user_id": 1, "email": "1@example.com", "role": "admin"}
        self.member = {"user_id": 2, "email": "2@example.com", "role": "member"}
        self.claims = self.admin
        for ident, role in [(1, "admin"), (2, "member"), (3, "member")]:
            self.session.add(UserEntity(id=ident, email=f"{ident}@example.com", name=role, role=role))
        self.session.commit()
        self.secret = '{"auths":{"registry.example":{"auth":"private-value"}}}'

    def test_registry_admin_only_redacted_and_cleared(self):
        with self.assertRaises(HTTPException) as error:
            get_registry_settings(self.session, self.member)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(HTTPException) as error:
            put_registry_settings(RegistryUpdate(dockerconfigjson=self.secret), self.session, self.member)
        self.assertEqual(error.exception.status_code, 403)
        result = put_registry_settings(RegistryUpdate(dockerconfigjson=self.secret), self.session, self.admin)
        self.assertEqual(result, {"configured": True})
        self.assertEqual(get_registry_settings(self.session, self.admin), {"configured": True})
        result = put_registry_settings(RegistryUpdate(dockerconfigjson=""), self.session, self.admin)
        self.assertEqual(result, {"configured": False})

    def test_invalid_registry_configs_rejected(self):
        for value in ["invalid", "[]", '{"auths":{}}', '{"auths":{"registry":"bad"}}']:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RegistryUpdate(dockerconfigjson=value)

    def test_all_workload_types_receive_central_secret_and_reject_override(self):
        put_registry_settings(RegistryUpdate(dockerconfigjson=self.secret), self.session, self.admin)
        for schema in [CreateDeploymentRequest, CreateCodeServerRequest, InstallMlflowRequest, StartModelPerfRequest]:
            with self.subTest(schema=schema):
                request = schema.model_construct(dockerconfigjson=None)
                self.assertEqual(apply_registry_settings(request, self.session).dockerconfigjson, self.secret)
                self.assertIsNone(request.dockerconfigjson)
                with self.assertRaises(BadRequestError):
                    apply_registry_settings(request.model_copy(update={"dockerconfigjson": self.secret}), self.session)

    def test_members_select_admin_profiles_without_receiving_secrets(self):
        create = CreateS3ProfileRequest(
            name="central", endpoint="https://s3.example.com", bucket="models", access_key="access", secret_key="secret"
        )
        profile = s3_profiles.create_profile(self.session, self.admin, create)
        self.session.add(
            S3ProfileEntity(
                owner_user_id=3, name="private", endpoint="https://private", bucket="private", access_key="x"
            )
        )
        self.session.commit()
        choices = s3_profiles.list_profiles(self.session, self.member)
        self.assertEqual([p.id for p in choices], [profile.id])
        self.assertEqual(choices[0].secret_key, "")
        for action in [
            lambda: s3_profiles.create_profile(self.session, self.member, create),
            lambda: s3_profiles.update_profile(
                self.session, self.member, profile.id, UpdateS3ProfileRequest(name="bad")
            ),
            lambda: s3_profiles.delete_profile(self.session, self.member, profile.id),
        ]:
            with self.assertRaises(ForbiddenError):
                action()
        request = CreateDeploymentRequest(
            deployment_name="test",
            image="triton",
            s3_profile_id=profile.id,
            repository_prefix="team/repository",
            model_control_mode="poll",
        )
        resolved = resolve_profile_request(request, self.session, self.member)
        self.assertEqual(resolved.s3_url, "s3://https://s3.example.com/models/team/repository")
        self.assertEqual(resolved.s3_secret_key, "secret")

    def test_deployment_manual_credentials_rejected(self):
        request = CreateDeploymentRequest(deployment_name="test", image="triton", s3_url="s3://bucket",
                                          s3_access_key="x", s3_secret_key="x")
        with self.assertRaises(BadRequestError):
            resolve_profile_request(request, self.session, self.member)
