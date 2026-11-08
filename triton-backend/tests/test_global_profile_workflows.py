"""Shared profile access retains per-user workflow credential isolation."""

import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import S3ProfileEntity, UserEntity
from app.exceptions import ConflictError, NotFoundError
from app.repositories import s3_profiles as profile_repository
from app.schemas import CreateS3ProfileRequest, UpdateS3ProfileRequest
from app.schemas.workflows import CreateWorkflowS3CredentialRequest
from app.services.storage import s3_profiles
from app.services.workflows import credentials, mlflow_delegation


class GlobalProfileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        for user_id in (1, 2):
            self.session.add(UserEntity(id=user_id, email=f"user{user_id}@example.com", name="User",
                                        role="member", auth_provider="local", is_active=True))
        self.profile = S3ProfileEntity(owner_user_id=1, name="dev", endpoint="https://s3",
                                       bucket="models", access_key="key", secret_key_enc="encrypted")
        self.session.add(self.profile)
        self.session.commit()
        for name, value in (("decrypt_secret", "secret"), ("_workflow_namespace", "ns"),
                            ("_secret_exists", False), ("_apply_secret", None),
                            ("sync_profile_credentials", None)):
            mock = patch.object(credentials, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def claims(self, user_id):
        return {"email": f"user{user_id}@example.com", "role": "member"}

    def link(self, user_id):
        return credentials.create_credential(
            CreateWorkflowS3CredentialRequest(name="dev (1)", s3_profile_id=self.profile.id),
            self.session, self.claims(user_id),
        )

    def test_two_users_can_link_same_profile_and_submit_with_their_own_repository(self):
        # Arrange
        first = self.link(1)
        # Act
        second = self.link(2)
        workflows = []
        for user_id in (1, 2):
            workflow = {"metadata": {"annotations": {"triton-control.ai/mlflow-s3-profile-name": "dev"}}}
            mlflow_delegation._select_artifact_repository(workflow, self.session, "ns", user_id)
            workflows.append(workflow)
        # Assert
        self.assertNotEqual(first.secret_name, second.secret_name)
        for user_id, linked, workflow in zip((1, 2), (first, second), workflows):
            self.assertEqual([row.id for row in credentials.list_credentials(self.session, self.claims(user_id))],
                             [linked.id])
            self.assertEqual(workflow["spec"]["artifactRepositoryRef"]["configMap"], linked.secret_name)

    def test_member_cannot_delete_another_users_link(self):
        # Arrange
        linked = self.link(1)
        with patch.object(credentials, "_delete_secret") as delete_secret:
            # Act
            with self.assertRaises(NotFoundError):
                credentials.delete_credential(self.session, linked.id, self.claims(2))
            # Assert
            delete_secret.assert_not_called()
            self.assertEqual(len(credentials.list_credentials(self.session, self.claims(1))), 1)

    def test_same_user_cannot_create_duplicate_link_name(self):
        # Arrange
        self.link(1)
        # Act
        with self.assertRaises(ConflictError):
            self.link(1)
        # Assert
        self.assertEqual(len(credentials.list_credentials(self.session, self.claims(1))), 1)

    def test_global_name_race_returns_conflict_and_keeps_session_usable(self):
        # Arrange: simulate a precheck made before a concurrent insert committed.
        claims = {**self.claims(2), "role": "admin"}
        request = CreateS3ProfileRequest(name="dev", endpoint="https://s3", bucket="models",
                                         access_key="key", secret_key="secret")
        existing = profile_repository.list_by_name(self.session, "dev")
        with patch.object(profile_repository, "list_by_name", side_effect=[[], existing]):
            # Act
            with self.assertRaises(ConflictError):
                s3_profiles.create_profile(self.session, claims, request)
        # Assert
        self.assertTrue(self.session.is_active)
        self.assertEqual(len(profile_repository.list_all(self.session)), 1)

    def test_global_rename_race_rolls_back_profile_and_keeps_session_usable(self):
        # Arrange
        other = profile_repository.create(self.session, owner_user_id=2, name="other",
                                           endpoint="https://s3", bucket="models", access_key="key")
        claims = {**self.claims(2), "role": "admin"}
        existing = profile_repository.list_by_name(self.session, "dev")
        with patch.object(profile_repository, "list_by_name", side_effect=[[], existing]):
            # Act
            with self.assertRaises(ConflictError):
                s3_profiles.update_profile(self.session, claims, other.id, UpdateS3ProfileRequest(name="dev"))
        # Assert
        self.assertTrue(self.session.is_active)
        self.session.refresh(other)
        self.assertEqual(other.name, "other")
