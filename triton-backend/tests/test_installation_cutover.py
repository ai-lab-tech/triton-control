"""AAA tests for fail-closed cutover and scope-preserving credential rotation."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core import installation_policy as policy
from app.db.entities import CodeServerEntity, LocalCredentialEntity, TritonInstanceEntity, UserEntity
from app.exceptions import ForbiddenError
from app.repositories import local_bindings
from app.repositories.installation_policy import cutover_blockers
from app.services import installation_policy as cutover
from app.services.storage.s3_profiles import update_profile


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.seed()

    def seed(self):
        mode = patch.object(policy, "mode", return_value="production")
        mode.start()
        self.addCleanup(mode.stop)
        with Session(self.engine) as session:
            session.add(UserEntity(id=1, email="admin@test", name="Admin", role="admin"))
            session.commit()

    def test_serving_records_do_not_block_cutover_or_get_deleted(self):
        # Arrange
        with Session(self.engine) as session:
            session.add(TritonInstanceEntity(name="serving", url="http://serving:8000"))
            session.commit()
            # Act
            blockers = cutover_blockers(session)
            # Assert
            self.assertEqual(blockers, [])
            self.assertEqual(len(session.exec(select(TritonInstanceEntity)).all()), 1)

    def test_existing_workspace_blocks_activation_without_deleting_it(self):
        # Arrange
        with Session(self.engine) as session:
            session.add(CodeServerEntity(owner_user_id=1, name="workspace", namespace="dev", statefulset_name="code-1",
                service_name="code-1", secret_name="code-1", image="fixture"))
            session.commit()
            # Act / Assert
            with patch.object(cutover, "verify_cluster_cutover") as cluster, \
                 patch.dict(os.environ, {"TRITON_BUILD_POLICY_FILE": "", "ARGO_WORKFLOWS_ENABLED": "false"}):
                with self.assertRaisesRegex(RuntimeError, "workspaces"):
                    cutover.verify_cutover(session)
                cluster.assert_not_called()
            self.assertIn("workspaces", cutover_blockers(session))

    def test_build_push_credentials_block_activation(self):
        # Arrange
        with Session(self.engine) as session:
            session.add(LocalCredentialEntity(name="push", provider="registry", scope={"permission": "push"},
                                              secret_enc="synthetic"))
            session.commit()
            # Act / Assert
            self.assertIn("build push credentials", cutover_blockers(session))

    def test_production_rotation_cannot_create_or_change_scope(self):
        # Arrange
        scope = {"repository": "https://git.test/source"}
        with Session(self.engine) as session:
            local_bindings.save_credential(session, "read", "git", scope, "old")
            # Act
            local_bindings.save_credential(session, "read", "git", scope, "rotated", require_existing=True)
            # Assert
            self.assertEqual(local_bindings.credential(session, "read").secret_enc, "rotated")
            with self.assertRaises(ForbiddenError):
                local_bindings.save_credential(session, "new", "git", scope, "secret", require_existing=True)
            session.rollback()
            with self.assertRaises(ForbiddenError):
                local_bindings.save_credential(session, "read", "git", {"repository": "https://elsewhere.test"},
                                                "secret", require_existing=True)
            session.rollback()
            self.assertEqual(local_bindings.credential(session, "read").scope, scope)

    def test_s3_scope_change_denied_before_sync_or_save(self):
        # Arrange
        from app.db.entities import S3ProfileEntity
        from app.schemas import UpdateS3ProfileRequest
        with Session(self.engine) as session:
            session.add(S3ProfileEntity(id=1, owner_user_id=1, name="artifacts", endpoint="https://s3.test",
                bucket="models", region="us-east-1", access_key="read", prefix="approved"))
            session.commit()
            # Act / Assert
            with self.assertRaises(ForbiddenError):
                update_profile(session, {"email": "admin@test", "role": "admin"}, 1,
                               UpdateS3ProfileRequest(prefix=""))
            session.rollback()
            self.assertEqual(session.get(S3ProfileEntity, 1).prefix, "approved")

    @patch.dict(os.environ, {"SESSION_SECRET": "installation-cutover-test-session"})
    def test_production_startup_only_starts_serving_health_worker(self):
        # Arrange
        import asyncio

        from app import main
        with patch.object(main, "init_db"), patch.object(main, "session_factory"), \
             patch.object(main, "verify_cutover") as verify, \
             patch.object(main, "sync_release_sources") as sync, \
             patch.object(main.instance_health_refresher, "start") as health, \
             patch.object(main.perf_job_reconciler, "start") as perf, \
             patch.object(main.operation_worker, "start") as operations, \
             patch.object(main.mlflow_migration, "start") as mlflow:
            # Act
            asyncio.run(main.on_startup())
            # Assert
            verify.assert_called_once()
            sync.assert_called_once()
            health.assert_called_once()
            perf.assert_not_called()
            operations.assert_not_called()
            mlflow.assert_not_called()

    def test_unavailable_cluster_inventory_blocks_cutover(self):
        # Arrange
        with patch.object(cutover, "is_running_in_cluster", return_value=True), \
             patch.object(cutover, "in_cluster_namespace", return_value="production"), \
             patch.object(cutover, "api_client", side_effect=OSError("sensitive provider details")):
            # Act / Assert
            with self.assertRaisesRegex(RuntimeError, "Unable to verify") as error:
                cutover.verify_cluster_cutover()
            self.assertNotIn("sensitive", str(error.exception))

    def test_legacy_first_login_cannot_bootstrap_admin_in_production(self):
        # Arrange
        from app.schemas import LoginRequest
        from app.services.auth import local_auth
        with patch.object(local_auth, "ensure_local_auth_allowed"), \
             patch.object(local_auth, "oidc_enabled", return_value=False), \
             patch.object(local_auth, "user_count", return_value=0), \
             patch.object(local_auth.users, "create") as create:
            # Act / Assert
            with Session(self.engine) as session, self.assertRaisesRegex(ForbiddenError, "first local admin"):
                local_auth.login(LoginRequest(email="attacker@test.test", password="irrelevant"), session)
            create.assert_not_called()

    def test_production_sign_up_waits_for_a_local_admin(self):
        from app.schemas import SelfRegisterRequest
        from app.services.auth import bootstrap, local_auth

        # Arrange
        request = SelfRegisterRequest(email="viewer@example.test", password="ViewerLocal123!")
        with (
            Session(self.engine) as session,
            patch.object(local_auth, "oidc_enabled", return_value=False),
            patch.object(bootstrap, "get_settings", return_value=SimpleNamespace(oidc_enabled=False)),
        ):
            # Act
            status = bootstrap.bootstrap_status(session)
            with self.assertRaisesRegex(ForbiddenError, "first local admin"):
                local_auth.self_register(request, session)

            # Assert
            self.assertTrue(status.local_admin_setup_required)
            self.assertFalse(status.needs_setup)
            self.assertEqual(local_auth.user_count(session), 1)

    def test_production_accepts_pending_signup_after_local_admin_setup(self):
        from app.schemas import SelfRegisterRequest
        from app.services.auth import local_auth

        # Arrange
        request = SelfRegisterRequest(email="viewer@example.test", password="ViewerLocal123!")
        with Session(self.engine) as session, patch.object(local_auth, "oidc_enabled", return_value=False):
            admin = session.get_one(UserEntity, 1)
            admin.password_hash = "existing-test-hash"
            session.commit()

            # Act
            user = local_auth.self_register(request, session)

            # Assert
            self.assertEqual(user.role, "viewer")
            self.assertFalse(user.is_active)

    def test_pending_deployment_blocks_cutover_but_terminal_history_does_not(self):
        from app.db.entities import OperationEntity
        from app.repositories.deployment_intents import KIND
        with Session(self.engine) as session:
            operation = OperationEntity(id="deployment", actor_id=1, kind=KIND, target="serving",
                                        idempotency_key="key", payload_hash="hash", payload={})
            session.add(operation)
            session.commit()
            self.assertIn("queued development operations", cutover_blockers(session))
            for state in ("succeeded", "failed", "cancelled"):
                operation.state = state
                session.add(operation)
                session.commit()
                self.assertNotIn("queued development operations", cutover_blockers(session))

    def test_unfinished_captures_block_cutover_but_terminal_history_does_not(self):
        from app.repositories.operations import HANDOFF_CAPTURE_KIND
        from app.db.entities import OperationEntity
        with Session(self.engine) as session:
            operation = OperationEntity(id="capture", actor_id=1, kind=HANDOFF_CAPTURE_KIND,
                                        target="model-preparation", idempotency_key="key",
                                        payload_hash="hash", payload={})
            for state in ("pending", "running", "succeeded", "failed", "cancelled"):
                operation.state = state
                session.add(operation)
                session.commit()
                self.assertEqual("queued development operations" in cutover_blockers(session),
                                 state in {"pending", "running"})

    @patch.dict(os.environ, {"SESSION_SECRET": "installation-cutover-test-session"})
    def test_production_shutdown_finishes_all_cleanup(self):
        import asyncio
        from app import main
        from unittest.mock import AsyncMock
        with patch.object(main.instance_health_refresher, "stop", new_callable=AsyncMock), \
             patch.object(main.operation_worker, "stop") as operations, \
             patch.object(main.build_context_retention, "stop") as retention, \
             patch.object(main.mlflow_migration, "stop", new_callable=AsyncMock) as mlflow, \
             patch.object(main.TritonService, "close_all_clients", new_callable=AsyncMock) as clients:
            asyncio.run(main.on_shutdown())
            operations.assert_called_once()
            retention.assert_called_once()
            mlflow.assert_awaited_once()
            clients.assert_awaited_once()

    def test_production_instance_rotation_preserves_scope_and_rejects_changes(self):
        from app.core.crypto import encrypt_secret, decrypt_secret, hash_secret
        from app.db.entities import S3ProfileEntity
        from app.schemas import UpdateInstanceS3Request, UpdateS3ProfileRequest
        from app.services.storage.s3 import update_instance_s3
        claims = {"email": "admin@test", "role": "admin"}
        with Session(self.engine) as session:
            session.add(S3ProfileEntity(id=1, owner_user_id=1, name="artifacts", endpoint="https://s3.test",
                bucket="models", region="us-east-1", access_key="old", prefix="approved",
                force_path_style=True, secret_key_enc=encrypt_secret("old-secret"),
                secret_key_hash=hash_secret("old-secret")))
            instance = TritonInstanceEntity(id=1, name="serving", url="http://serving:8000",
                s3_enabled=True, s3_endpoint="https://s3.test", s3_bucket="models", s3_region="us-east-1",
                s3_prefix="approved/model", s3_access_key="old", s3_secret_key_enc=encrypt_secret("old-secret"),
                s3_use_https=True, s3_verify_ssl=True, s3_address_style="path")
            session.add(instance)
            session.commit()
            update_profile(session, claims, 1, UpdateS3ProfileRequest(access_key="new", secret_key="new-secret"))
            session.refresh(instance)
            before = instance.model_dump(exclude={"s3_access_key", "s3_secret_key_hash", "s3_secret_key_enc"})
            request = UpdateInstanceS3Request(enabled=True, s3_profile_id=1)
            update_instance_s3(session, claims, 1, request)
            self.assertEqual(instance.s3_access_key, "new")
            self.assertEqual(decrypt_secret(instance.s3_secret_key_enc), "new-secret")
            self.assertEqual(instance.model_dump(exclude={"s3_access_key", "s3_secret_key_hash", "s3_secret_key_enc"}), before)
            with self.assertRaises(ForbiddenError):
                update_instance_s3(session, {**claims, "role": "member"}, 1, request)
            for extra in ({"enabled": False}, {"prefix": "elsewhere"}, {"verify_ssl": False}):
                with self.assertRaises(ForbiddenError):
                    update_instance_s3(session, claims, 1, request.model_copy(update=extra))
            profile = session.get(S3ProfileEntity, 1)
            for field, value in (("bucket", "other"), ("prefix", "approved-other"), ("ca_certificate", "other")):
                old = getattr(profile, field)
                setattr(profile, field, value)
                session.commit()
                with self.assertRaises(ForbiddenError):
                    update_instance_s3(session, claims, 1, request)
                setattr(profile, field, old)
                session.commit()
