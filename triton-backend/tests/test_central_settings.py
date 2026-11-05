"""AAA tests for central credential authorization, persistence and workload wiring."""

import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.registry_api import get_registry_settings, put_registry_settings
from app.db.entities import RegistryConfigEntity, S3ProfileEntity, TritonInstanceEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.repositories import s3_profiles as profile_repository
from app.schemas import CreateDeploymentRequest, CreateS3ProfileRequest, UpdateS3ProfileRequest
from app.schemas.development import CreateCodeServerRequest
from app.schemas.instance import UpdateInstanceS3Request
from app.schemas.mlflow import InstallMlflowRequest
from app.schemas.perf_jobs import StartModelPerfRequest
from app.schemas.registry import RegistryUpdate
from app.services import registry_settings
from app.services.deployment import deployment
from app.services.deployment.profile_request import resolve_profile_request
from app.services.development import workspaces
from app.services.mlflow import installer
from app.services.perf_analyzer import jobs
from app.services.storage import s3, s3_profiles


class CentralSettingsTests(unittest.TestCase):
    def setUp(self):
        # Arrange: isolated persistence and encryption key, with admin/member identities.
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
        for ident, role in [(1, "admin"), (2, "member"), (3, "member")]:
            self.session.add(UserEntity(id=ident, email=f"{ident}@example.com", name=role, role=role))
        self.session.commit()
        self.secret = '{"auths":{"registry.example":{"auth":"private-value"}}}'

    def save_registry(self):
        registry_settings.update_settings(RegistryUpdate(dockerconfigjson=self.secret), self.session, self.admin)

    def profile(self, owner=1, name="central"):
        row = S3ProfileEntity(
            owner_user_id=owner,
            name=name,
            endpoint="https://s3.example.com",
            bucket="models",
            access_key="access",
            secret_key_enc="storage-secret",
        )
        self.session.add(row)
        self.session.commit()
        return row

    def test_member_cannot_read_registry_status(self):
        # Arrange
        claims = self.member
        # Act
        with self.assertRaises(HTTPException) as error:
            get_registry_settings(self.session, claims)
        # Assert
        self.assertEqual(error.exception.status_code, 403)

    def test_member_cannot_change_registry(self):
        # Arrange
        request = RegistryUpdate(dockerconfigjson=self.secret)
        # Act
        with self.assertRaises(HTTPException) as error:
            put_registry_settings(request, self.session, self.member)
        # Assert
        self.assertEqual(error.exception.status_code, 403)
        self.assertIsNone(self.session.get(RegistryConfigEntity, 1))

    def test_save_registry_encrypts_at_rest_and_redacts_response(self):
        # Arrange
        request = RegistryUpdate(dockerconfigjson=self.secret)
        # Act
        result = put_registry_settings(request, self.session, self.admin)
        # Assert
        row = self.session.get(RegistryConfigEntity, 1)
        self.assertIsNotNone(row)
        self.assertNotIn("private-value", row.dockerconfigjson_enc)
        self.assertEqual(registry_settings.decrypt_registry_secret(row.dockerconfigjson_enc), self.secret)
        self.assertEqual(result, {"configured": True})

    def test_registry_status_does_not_return_credentials(self):
        # Arrange
        self.save_registry()
        # Act
        result = get_registry_settings(self.session, self.admin)
        # Assert
        self.assertEqual(result, {"configured": True})

    def test_remove_registry_clears_persisted_credentials(self):
        # Arrange
        self.save_registry()
        # Act
        result = put_registry_settings(RegistryUpdate(dockerconfigjson=""), self.session, self.admin)
        # Assert
        self.assertEqual(result, {"configured": False})
        self.assertEqual(self.session.get(RegistryConfigEntity, 1).dockerconfigjson_enc, "")

    def test_invalid_registry_configs_rejected(self):
        for value in ["invalid", "[]", '{"auths":{}}', '{"auths":{"registry":"bad"}}']:
            with self.subTest(value=value):
                # Arrange / Act
                with self.assertRaises(ValidationError) as error:
                    RegistryUpdate(dockerconfigjson=value)
                # Assert
                self.assertEqual(error.exception.errors()[0]["loc"], ("dockerconfigjson",))

    def test_member_choices_include_shared_and_owned_profiles_only(self):
        # Arrange
        shared = self.profile()
        owned = self.profile(owner=2, name="owned")
        self.profile(owner=3, name="private")
        # Act
        choices = s3_profiles.list_profiles(self.session, self.member)
        # Assert
        self.assertEqual({p.id for p in choices}, {shared.id, owned.id})
        self.assertTrue(all(p.secret_key == "" for p in choices))

    def test_admin_can_resolve_every_offered_profile(self):
        # Arrange
        self.profile()
        self.profile(owner=2, name="legacy")
        choices = s3_profiles.list_profiles(self.session, self.admin)
        # Act
        resolved = [
            resolve_profile_request(
                CreateDeploymentRequest(
                    deployment_name=f"test-{p.id}",
                    image="triton",
                    s3_profile_id=p.id,
                ),
                self.session,
                self.admin,
            )
            for p in choices
        ]
        # Assert
        self.assertEqual(len(resolved), 2)
        self.assertTrue(all(request.s3_secret_key == "storage-secret" for request in resolved))

    def test_member_cannot_resolve_another_members_profile(self):
        # Arrange
        private = self.profile(owner=3)
        request = CreateDeploymentRequest(deployment_name="test", image="triton", s3_profile_id=private.id)
        # Act
        with self.assertRaises(NotFoundError) as error:
            resolve_profile_request(request, self.session, self.member)
        # Assert
        self.assertIn("S3 profile not found", str(error.exception))

    def test_admin_rename_checks_the_profile_owners_namespace(self):
        # Arrange
        original = self.profile(owner=2, name="original")
        self.profile(owner=2, name="occupied")
        # Act
        with self.assertRaises(ConflictError):
            s3_profiles.update_profile(self.session, self.admin, original.id, UpdateS3ProfileRequest(name="occupied"))
        # Assert: no failed transaction or partially changed row.
        self.assertEqual(profile_repository.get(self.session, original.id).name, "original")
        self.assertTrue(self.session.is_active)

    def test_admin_rename_does_not_conflict_with_admins_own_profile(self):
        # Arrange
        original = self.profile(owner=2, name="original")
        self.profile(owner=1, name="admin-name")
        # Act
        result = s3_profiles.update_profile(
            self.session, self.admin, original.id, UpdateS3ProfileRequest(name="admin-name")
        )
        # Assert
        self.assertEqual(result.name, "admin-name")
        self.assertEqual(profile_repository.get(self.session, original.id).owner_user_id, 2)

    def test_member_cannot_create_profile(self):
        # Arrange
        request = CreateS3ProfileRequest(
            name="new", endpoint="https://s3", bucket="models", access_key="access", secret_key="secret"
        )
        # Act
        with self.assertRaises(ForbiddenError):
            s3_profiles.create_profile(self.session, self.member, request)
        # Assert
        self.assertEqual(list(self.session.exec(select(S3ProfileEntity)).all()), [])

    def test_member_cannot_update_profile(self):
        # Arrange
        profile = self.profile()
        # Act
        with self.assertRaises(ForbiddenError):
            s3_profiles.update_profile(self.session, self.member, profile.id, UpdateS3ProfileRequest(name="changed"))
        # Assert
        self.assertEqual(profile_repository.get(self.session, profile.id).name, "central")

    def test_member_cannot_delete_profile(self):
        # Arrange
        profile = self.profile()
        # Act
        with self.assertRaises(ForbiddenError):
            s3_profiles.delete_profile(self.session, self.member, profile.id)
        # Assert
        self.assertIsNotNone(profile_repository.get(self.session, profile.id))

    def test_deployment_resolves_storage_and_registry_before_scheduling(self):
        # Arrange: real service, repositories and database; only scheduling is mocked.
        self.save_registry()
        profile = self.profile(owner=2)
        request = CreateDeploymentRequest(
            deployment_name="test",
            image="triton",
            s3_profile_id=profile.id,
            repository_prefix="repository",
            model_control_mode="poll",
        )
        scheduled = []
        # Act
        response = deployment.create_deployment(
            request, self.session, self.admin, lambda fn, *args: scheduled.append(args)
        )
        # Assert
        self.assertEqual(len(scheduled), 1)
        resolved = scheduled[0][1]
        self.assertEqual(resolved.dockerconfigjson, self.secret)
        self.assertEqual(resolved.s3_url, "s3://https://s3.example.com/models/repository")
        self.assertEqual(resolved.s3_secret_key, "storage-secret")
        self.assertIsNone(request.dockerconfigjson)
        self.assertNotIn("private-value", response.model_dump_json())

    def test_deployment_rejects_registry_override_before_creating_records(self):
        # Arrange
        profile = self.profile()
        request = CreateDeploymentRequest(
            deployment_name="test", image="triton", s3_profile_id=profile.id, dockerconfigjson=self.secret
        )
        # Act
        with self.assertRaises(BadRequestError):
            deployment.create_deployment(request, self.session, self.admin)
        # Assert
        self.assertEqual(list(self.session.exec(select(TritonInstanceEntity)).all()), [])

    def test_deployment_rejects_manual_storage_before_scheduling(self):
        # Arrange
        request = CreateDeploymentRequest(
            deployment_name="test", image="triton", s3_url="s3://bucket", s3_access_key="x", s3_secret_key="x"
        )
        scheduled = []
        # Act
        with self.assertRaises(BadRequestError):
            deployment.create_deployment(request, self.session, self.admin, lambda *args: scheduled.append(args))
        # Assert
        self.assertEqual(scheduled, [])

    def test_workspace_creation_uses_central_registry(self):
        # Arrange
        self.save_registry()
        request = CreateCodeServerRequest()
        with (
            patch.object(workspaces, "_require_kubernetes_enabled"),
            patch.object(workspaces.k8s, "apply_code_server_resources", return_value=[]) as apply,
        ):
            # Act
            response = workspaces.create_code_server(request, self.session, self.member)
        # Assert
        self.assertEqual(apply.call_args.args[0].dockerconfigjson, self.secret)
        self.assertNotIn("private-value", response.model_dump_json())

    def test_workspace_creation_rejects_registry_override(self):
        # Arrange
        request = CreateCodeServerRequest(dockerconfigjson=self.secret)
        with (
            patch.object(workspaces, "_require_kubernetes_enabled"),
            patch.object(workspaces.k8s, "apply_code_server_resources") as apply,
        ):
            # Act
            with self.assertRaises(BadRequestError):
                workspaces.create_code_server(request, self.session, self.member)
        # Assert
        apply.assert_not_called()

    def test_mlflow_creation_uses_central_registry_and_admin_visible_profile(self):
        # Arrange
        self.save_registry()
        profile = self.profile(owner=2)
        request = InstallMlflowRequest(s3_profile_id=profile.id)
        with (
            patch.object(installer.mlflow, "prepare_backend_store", return_value="postgresql://test"),
            patch.object(installer.k8s, "apply_installation_resources", return_value=[]) as apply,
        ):
            # Act
            response = installer.install_mlflow(request, self.session, self.admin)
        # Assert
        self.assertEqual(apply.call_args.args[0].dockerconfigjson, self.secret)
        self.assertEqual(response.s3_profile_id, profile.id)
        self.assertNotIn("private-value", response.model_dump_json())

    def test_mlflow_creation_rejects_registry_override(self):
        # Arrange
        profile = self.profile()
        request = InstallMlflowRequest(s3_profile_id=profile.id, dockerconfigjson=self.secret)
        with patch.object(installer.k8s, "apply_installation_resources") as apply:
            # Act
            with self.assertRaises(BadRequestError):
                installer.install_mlflow(request, self.session, self.admin)
        # Assert
        apply.assert_not_called()

    def test_perf_job_creation_uses_central_registry(self):
        # Arrange
        self.save_registry()
        self.session.add(TritonInstanceEntity(id=1, name="test", url="http://triton"))
        self.session.commit()
        request = StartModelPerfRequest(model_version="1")
        with (
            patch.object(jobs.commands, "fetch_triton_model_config", return_value={"backend": "onnxruntime"}),
            patch.object(jobs, "Jobs") as api,
        ):
            # Act
            response = jobs.start(self.session, self.admin, 1, "model", request)
        # Assert
        self.assertEqual(api.return_value.prepare.call_args.args[1], self.secret)
        self.assertEqual(response.state, "creating")
        self.assertNotIn("private-value", response.model_dump_json())

    def test_perf_job_creation_rejects_registry_override(self):
        # Arrange
        self.session.add(TritonInstanceEntity(id=1, name="test", url="http://triton"))
        self.session.commit()
        request = StartModelPerfRequest(model_version="1", dockerconfigjson=self.secret)
        with patch.object(jobs, "Jobs") as api:
            # Act
            with self.assertRaises(BadRequestError):
                jobs.start(self.session, self.admin, 1, "model", request)
        # Assert
        api.assert_not_called()

    def test_admin_instance_connection_accepts_legacy_profile(self):
        # Arrange
        profile = self.profile(owner=2)
        self.session.add(TritonInstanceEntity(id=1, name="test", url="http://triton"))
        self.session.commit()
        request = UpdateInstanceS3Request(enabled=True, s3_profile_id=profile.id, prefix="repository")
        # Act
        result = s3.update_instance_s3(self.session, self.admin, 1, request)
        # Assert
        self.assertEqual(result.bucket, "models")
        self.assertTrue(result.secret_configured)
