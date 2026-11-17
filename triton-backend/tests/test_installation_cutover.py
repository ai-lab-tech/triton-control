"""AAA tests for fail-closed cutover and scope-preserving credential rotation."""

import os
import unittest
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

    def test_production_startup_only_starts_serving_health_worker(self):
        # Arrange
        import asyncio

        from app import main
        with patch.object(main, "init_db"), patch.object(main, "session_factory"), \
             patch.object(main, "verify_cutover") as verify, \
             patch.object(main.instance_health_refresher, "start") as health, \
             patch.object(main.perf_job_reconciler, "start") as perf, \
             patch.object(main.operation_worker, "start") as operations, \
             patch.object(main.mlflow_migration, "start") as mlflow:
            # Act
            asyncio.run(main.on_startup())
            # Assert
            verify.assert_called_once()
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
            with Session(self.engine) as session, self.assertRaises(ForbiddenError):
                local_auth.login(LoginRequest(email="attacker@test.test", password="irrelevant"), session)
            create.assert_not_called()
