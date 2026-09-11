"""Tests for reusable S3 deployment profiles."""

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import workflows_api
from app.db.entities import S3ProfileEntity, UserEntity, WorkflowS3CredentialEntity
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.schemas import CreateS3ProfileRequest, UpdateS3ProfileRequest
from app.schemas.workflows import CreateWorkflowS3CredentialRequest
from app.services.storage import s3_profiles
from app.services.workflows import credentials


class S3ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(self.engine)

    def _session(self) -> Session:
        return Session(self.engine)

    def _claims(self) -> dict[str, object]:
        return {"user_id": 7, "email": "member@example.com", "role": "member"}

    def _create_user(self, session: Session) -> None:
        session.add(
            UserEntity(
                id=7,
                email="member@example.com",
                name="Member",
                role="member",
                auth_provider="local",
                is_active=True,
            )
        )
        session.commit()

    def test_CreateAndListProfiles_ReturnsOwnedCredentials(self) -> None:
        with self._session() as session:
            self._create_user(session)

            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                    prefix="dev",
                ),
            )
            listed = s3_profiles.list_profiles(session, self._claims())

        self.assertEqual(created.endpoint, "https://minio:9000")
        self.assertEqual(created.secret_key, "secret")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].name, "team-minio")
        self.assertEqual(listed[0].secret_key, "secret")

    def test_UpdateProfile_ChangesVisibleFieldsAndCanKeepSecret(self) -> None:
        with self._session() as session:
            self._create_user(session)
            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="https://minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                ),
            )

            updated = s3_profiles.update_profile(
                session,
                self._claims(),
                created.id,
                UpdateS3ProfileRequest(name="prod-minio", bucket="prod-models"),
            )

        self.assertEqual(updated.name, "prod-minio")
        self.assertEqual(updated.bucket, "prod-models")
        self.assertEqual(updated.secret_key, "secret")

    def test_DeleteProfile_RemovesOwnedRow(self) -> None:
        with self._session() as session:
            self._create_user(session)
            created = s3_profiles.create_profile(
                session,
                self._claims(),
                CreateS3ProfileRequest(
                    name="team-minio",
                    endpoint="https://minio:9000",
                    bucket="models",
                    access_key="access",
                    secret_key="secret",
                ),
            )

            result = s3_profiles.delete_profile(session, self._claims(), created.id)
            remaining = session.exec(select(S3ProfileEntity)).all()

        self.assertEqual(result, {"status": "deleted"})
        self.assertEqual(remaining, [])

    def test_LinkedCredentials_RotateRetryAndProtectDeletion(self) -> None:
        from kubernetes.client import V1ConfigMap, V1ObjectMeta, V1Secret  # type: ignore[import-untyped]
        from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
        with self._session() as session:
            self._create_user(session)
            profile = s3_profiles.create_profile(session, self._claims(), CreateS3ProfileRequest(
                name="linked", endpoint="https://minio:9000", bucket="models",
                access_key="old-access", secret_key="old-secret",
            ))
            core = MagicMock()
            body = V1Secret(metadata=V1ObjectMeta(labels={
                "app.kubernetes.io/managed-by": "triton-control",
                "triton-control/component": "workflow-s3-credential",
            }), data={"ca.pem": "old-ca", "unrelated": "keep"})
            core.read_namespaced_secret.return_value = body
            maps: dict[str, V1ConfigMap] = {}

            def read_map(name: str, namespace: str) -> V1ConfigMap:
                if name not in maps:
                    raise ApiException(status=404)
                return maps[name]

            def create_map(namespace: str, body: V1ConfigMap) -> None:
                maps[body.metadata.name] = body

            core.read_namespaced_config_map.side_effect = read_map
            core.create_namespaced_config_map.side_effect = create_map
            with (patch("app.services.workflows.credentials._secret_exists", return_value=False),
                  patch("app.services.workflows.credentials.api_client"),
                  patch("app.services.workflows.artifact_repository.api_client"),
                  patch("kubernetes.client.CoreV1Api", return_value=core)):
                created = credentials.create_credential(CreateWorkflowS3CredentialRequest(
                    name="linked-secret", s3_profile_id=profile.id,
                ), session, self._claims())
                self.assertEqual(created.s3_profile_id, profile.id)
                self.assertEqual(created.artifact_repository_config_map, created.secret_name)
                repository = maps[created.secret_name]
                self.assertIsInstance(repository, V1ConfigMap)
                config = json.loads(repository.data["repository"])["s3"]
                self.assertEqual(config["endpoint"], "minio:9000")
                self.assertFalse(config["insecure"])
                self.assertNotIn("old-secret", repository.data["repository"])
                self.assertIsNotNone(created.last_synced_at)
                self.assertEqual(created.sync_error, "")
                self.assertNotIn("ca.pem", body.data)
                self.assertEqual(body.data["unrelated"], "keep")
                credentials._replace_secret(session.get(WorkflowS3CredentialEntity, created.id),
                    CreateWorkflowS3CredentialRequest(name="test", access_key_id="key", secret_access_key="secret")
                    .model_copy(update={"ca_certificate": "public-ca"}))
                self.assertEqual(base64.b64decode(body.data["ca.pem"]), b"public-ca")
                s3_profiles.update_profile(session, self._claims(), profile.id, UpdateS3ProfileRequest(
                    access_key="new-access", secret_key="new-secret", name="renamed",
                    endpoint="https://other:9443", bucket="new-bucket", region="eu-west-1",
                ))
                self.assertEqual(base64.b64decode(body.data["secret-access-key"]), b"new-secret")
                row = session.get(WorkflowS3CredentialEntity, created.id)
                self.assertNotIn("ca.pem", body.data)
                self.assertEqual(row.secret_name, created.secret_name)
                self.assertEqual(row.access_key_id, "new-access")
                self.assertEqual(row.s3_profile_name, "renamed")
                config = json.loads(repository.data["repository"])["s3"]
                self.assertEqual(config["endpoint"], "other:9443")
                self.assertEqual(config["bucket"], "new-bucket")
                self.assertEqual(config["region"], "eu-west-1")
                synced_at = row.last_synced_at
                core.replace_namespaced_config_map.side_effect = RuntimeError("DO-NOT-LEAK-secret")
                result = credentials.retry_sync(session, self._claims(), created.id)
                self.assertTrue(result.sync_error)
                self.assertNotIn("DO-NOT-LEAK", result.sync_error)
                self.assertEqual(result.last_synced_at, synced_at)
                core.replace_namespaced_config_map.side_effect = None
                core.replace_namespaced_secret.side_effect = RuntimeError("DO-NOT-LEAK-secret")
                s3_profiles.update_profile(session, self._claims(), profile.id, UpdateS3ProfileRequest(
                    secret_key="retry-secret",
                ))
                session.refresh(row)
                self.assertTrue(row.sync_error)
                self.assertNotIn("DO-NOT-LEAK", row.sync_error)
                self.assertEqual(row.last_synced_at, synced_at)
                core.replace_namespaced_secret.side_effect = None
                result = credentials.retry_sync(session, self._claims(), created.id)
                self.assertEqual(result.sync_error, "")
                self.assertEqual(base64.b64decode(body.data["secret-access-key"]), b"retry-secret")
                with self.assertRaises(ConflictError):
                    s3_profiles.delete_profile(session, self._claims(), profile.id)
                credentials.delete_credential(session, created.id)
                core.delete_namespaced_config_map.assert_called_once()
                self.assertEqual(s3_profiles.delete_profile(session, self._claims(), profile.id), {"status": "deleted"})

    def test_ProfileChoices_OwnedMetadataOnlyAndForeignLinkRejected(self) -> None:
        with self._session() as session:
            self._create_user(session)
            owned = s3_profiles.create_profile(session, self._claims(), CreateS3ProfileRequest(
                name="owned", endpoint="https://minio", bucket="models", access_key="key", secret_key="secret",
            ))
            foreign = S3ProfileEntity(owner_user_id=99, name="foreign", endpoint="https://minio", bucket="models",
                                      access_key="foreign-key", secret_key_enc="foreign-secret")
            session.add(foreign)
            session.commit()
            choices = workflows_api.list_s3_profile_choices(session=session, claims=self._claims())
            self.assertEqual([p.id for p in choices], [owned.id])
            self.assertEqual(set(choices[0].model_dump()), {"id", "name", "endpoint", "bucket", "region"})
            with patch("app.services.workflows.credentials._apply_secret") as apply:
                with self.assertRaises(NotFoundError):
                    credentials.create_credential(CreateWorkflowS3CredentialRequest(
                        name="forbidden", s3_profile_id=foreign.id,
                    ), session, self._claims())
                apply.assert_not_called()
            foreign_link = WorkflowS3CredentialEntity(
                created_by_user_id=99, name="foreign-link", namespace="ns", secret_name="foreign-secret",
                access_key_id="key", s3_profile_id=foreign.id,
            )
            session.add(foreign_link)
            session.commit()
            with patch("app.services.workflows.credentials.sync_profile_credentials") as sync:
                with self.assertRaises(NotFoundError):
                    credentials.retry_sync(session, self._claims(), foreign_link.id)
                sync.assert_not_called()

    def test_LinkMalformedCaExplainsPemFormatWithoutCreatingSecret(self) -> None:
        with self._session() as session:
            self._create_user(session)
            profile = s3_profiles.create_profile(session, self._claims(), CreateS3ProfileRequest(
                name="bad-ca", endpoint="https://minio", bucket="models", access_key="key", secret_key="private-value",
            ))
            # Simulate a legacy profile saved before certificate validation existed.
            row = session.get(S3ProfileEntity, profile.id)
            row.ca_certificate = "----BEGIN CERTIFICATE-----\nbad\n-----END CERTIFICATE-----"
            session.add(row)
            session.commit()
            with patch("app.services.workflows.credentials._apply_secret") as apply:
                with self.assertRaises(BadRequestError) as raised:
                    credentials.create_credential(CreateWorkflowS3CredentialRequest(
                        name="bad-ca-link", s3_profile_id=profile.id,
                    ), session, self._claims())
                self.assertIn("five dashes", raised.exception.detail)
                self.assertNotIn("private-value", raised.exception.detail)
                apply.assert_not_called()

    def test_ProfileRequestsRejectInvalidCertificatesAndAllowClearing(self) -> None:
        for invalid in ["----BEGIN CERTIFICATE-----\nbad\n-----END CERTIFICATE-----",
                        "-----BEGIN CERTIFICATE-----\nbad\n-----END CERTIFICATE-----",
                        "-----BEGIN PRIVATE KEY-----\nbad\n-----END PRIVATE KEY-----", "x" * 262145]:
            with self.subTest(certificate=invalid[:25]):
                with self.assertRaises(ValueError):
                    CreateS3ProfileRequest(name="test", endpoint="https://minio", bucket="models",
                                           access_key="key", secret_key="secret", ca_certificate=invalid)
                with self.assertRaises(ValueError):
                    UpdateS3ProfileRequest(ca_certificate=invalid)
        self.assertIsNone(UpdateS3ProfileRequest().ca_certificate)
        self.assertIsNone(UpdateS3ProfileRequest(ca_certificate=None).ca_certificate)
        self.assertEqual(UpdateS3ProfileRequest(ca_certificate="  ").ca_certificate, "")

    def test_SyncRejectsUnmanagedSecret(self) -> None:
        from kubernetes.client import V1ObjectMeta, V1Secret
        core = MagicMock()
        core.read_namespaced_secret.return_value = V1Secret(metadata=V1ObjectMeta(labels={}))
        row = WorkflowS3CredentialEntity(name="test", namespace="ns", secret_name="test", access_key_id="key",
                                         created_by_user_id=7)
        with (patch("app.services.workflows.credentials.api_client"),
              patch("kubernetes.client.CoreV1Api", return_value=core)):
            with self.assertRaises(ConflictError):
                credentials._replace_secret(row, CreateWorkflowS3CredentialRequest(
                    name="test", access_key_id="key", secret_access_key="secret",
                ))
        core.replace_namespaced_secret.assert_not_called()


if __name__ == "__main__":
    unittest.main()
