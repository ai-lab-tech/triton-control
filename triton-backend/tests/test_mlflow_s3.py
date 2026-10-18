"""S3 installation contracts and server-side credentials."""

import asyncio
import json
import unittest
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import Request
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from pydantic import ValidationError
from sqlalchemy.engine import make_url

from app.db.entities import MlflowEntity, S3ProfileEntity
from app.exceptions import BadGatewayError, BadRequestError, ConflictError, NotFoundError
from app.repositories import mlflow
from app.schemas import InstallMlflowRequest, UpdateS3ProfileRequest
from app.services.mlflow import installer, kubernetes, proxy
from app.services.storage import s3_profiles


class MlflowS3Tests(unittest.TestCase):
    def profile(self):
        return S3ProfileEntity(
            id=3, owner_user_id=7, name="artifacts", endpoint="https://s3.example",
            bucket="models", prefix="team", region="eu-central-1", access_key="fixture-access",
            secret_key_enc="encrypted", force_path_style=True, ca_certificate="fixture-ca",
        )

    def test_install_requires_positive_profile_id(self):
        for payload in ({}, {"s3_profile_id": 0}, {"s3_profile_id": -1}):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                InstallMlflowRequest.model_validate(payload)

    def test_other_users_profile_is_rejected_before_creating_resources(self):
        with patch.object(installer.mlflow, "get", return_value=None), patch.object(
            installer, "require_user_entity", return_value=SimpleNamespace(id=7)
        ), patch.object(installer.s3_profiles, "find_for_owner", return_value=None) as lookup, patch.object(
            installer.k8s, "apply_installation_resources"
        ) as apply:
            with self.assertRaises(NotFoundError):
                installer.install_mlflow(InstallMlflowRequest(s3_profile_id=3), MagicMock(), {"role": "member"})
            lookup.assert_called_once_with(unittest.mock.ANY, 7, 3)
            apply.assert_not_called()

    def test_s3_install_has_no_pvc_and_credentials_only_in_secret(self):
        with patch.object(kubernetes, "decrypt_secret", return_value="fixture-secret"):
            storage = kubernetes.storage_configuration(self.profile(), "postgresql://fixture")
        manifests = kubernetes._manifests(
            InstallMlflowRequest(s3_profile_id=3), "control", "mlflow", "mlflow-service", storage=storage,
        )
        self.assertFalse(any(item["kind"] == "PersistentVolumeClaim" for item in manifests))
        deployment = next(item for item in manifests if item["kind"] == "Deployment")
        self.assertNotIn("fixture-secret", json.dumps(deployment))
        self.assertNotIn("fixture-access", json.dumps(deployment))
        pod = deployment["spec"]["template"]["spec"]
        self.assertFalse(any("persistentVolumeClaim" in volume for volume in pod["volumes"]))
        self.assertEqual(pod["containers"][0]["readinessProbe"]["exec"]["command"][:2], ["python", "-c"])
        secret = next(item for item in manifests if item["metadata"]["name"] == "mlflow-storage")
        self.assertEqual(secret["stringData"]["MLFLOW_ARTIFACTS_DESTINATION"], "s3://models/team/mlflow")
        self.assertEqual(secret["stringData"]["AWS_SECRET_ACCESS_KEY"], "fixture-secret")
        self.assertEqual(secret["stringData"]["AWS_CA_BUNDLE"], "/etc/mlflow-storage/ca.pem")
        self.assertIn("addressing_style = path", secret["stringData"]["aws-config"])
        self.assertIn('--artifacts-destination "$MLFLOW_ARTIFACTS_DESTINATION"', pod["containers"][0]["args"][0])

    def test_backend_uses_separate_schema_and_preserves_connection_options(self):
        # Arrange
        session = MagicMock()
        session.connection.return_value.engine.url = make_url("postgresql://user:p%40ss@postgres/db?sslmode=require")

        # Act
        uri = mlflow.prepare_backend_store(session)

        # Assert
        parsed = make_url(uri)
        self.assertEqual(parsed.password, "p@ss")
        self.assertEqual(parsed.query["sslmode"], "require")
        self.assertEqual(parsed.query["options"], "-csearch_path=mlflow_tracking")
        self.assertIn("CREATE SCHEMA", str(session.execute.call_args.args[0]))

    def test_backend_rejects_sqlite_before_creating_schema(self):
        # Arrange
        session = MagicMock()
        session.connection.return_value.engine.url = make_url("sqlite:///local.db")

        # Act / Assert
        with self.assertRaises(BadRequestError):
            mlflow.prepare_backend_store(session)
        session.execute.assert_not_called()
        session.commit.assert_not_called()

    def test_profile_in_use_cannot_be_deleted_or_changed(self):
        with patch.object(s3_profiles, "require_user_entity", return_value=SimpleNamespace(id=7)), patch.object(
            s3_profiles, "_get_profile", return_value=self.profile()
        ), patch.object(s3_profiles.mlflow, "get", return_value=SimpleNamespace(s3_profile_id=3)):
            with self.assertRaises(ConflictError):
                s3_profiles.delete_profile(MagicMock(), {"role": "member"}, 3)
            with self.assertRaises(ConflictError):
                s3_profiles.update_profile(MagicMock(), {"role": "member"}, 3, UpdateS3ProfileRequest(bucket="other"))

    def test_s3_upgrade_never_falls_back_to_sqlite_when_secret_is_missing(self):
        with patch.object(kubernetes, "_client"), patch("kubernetes.client.CoreV1Api") as core, patch.object(
            kubernetes, "apply_installation_resources"
        ) as apply:
            core.return_value.read_namespaced_secret.side_effect = ApiException(status=404)
            with self.assertRaises(BadGatewayError):
                kubernetes.upgrade_installation_resources(
                    "control", "mlflow", "mlflow-service",
                )
            apply.assert_not_called()

    def test_artifact_discovery_returns_only_persisted_nonsecret_root(self):
        # Arrange
        request_thread = get_ident()
        database_threads = []
        session = MagicMock()
        session.__enter__.return_value = session
        request = Request({"type": "http", "method": "GET", "headers": []})

        def open_session():
            database_threads.append(get_ident())
            return session

        with patch.object(proxy, "session_factory", side_effect=open_session), patch.object(
            proxy.mlflow, "get", return_value=MlflowEntity(
                namespace="control", deployment_name="mlflow", service_name="mlflow-service", image="mlflow:test",
                s3_profile_id=3, artifact_root="s3://models/team/mlflow",
            )
        ), patch.object(proxy, "_proxy_http_sync") as upstream:
            # Act
            response = asyncio.run(proxy.proxy_http(
                "api/2.0/mlflow/triton-control/artifact-store", request, {"role": "member"},
            ))

        # Assert
        self.assertEqual(json.loads(response.body), {"artifact_root": "s3://models/team/mlflow"})
        self.assertEqual(len(database_threads), 1)
        self.assertNotEqual(database_threads[0], request_thread)
        upstream.assert_not_called()
