"""Owner isolation and model URI checks for MLflow-backed Triton deployments."""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import S3ProfileEntity, TritonInstanceEntity, UserEntity
from app.exceptions import BadRequestError, NotFoundError
from app.schemas import CreateDeploymentRequest
from app.services.deployment import mlflow, profile_request


class ProfileDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member"))
            session.add(UserEntity(id=8, email="other@example.com", name="Other", role="member"))
            session.add(S3ProfileEntity(
                id=11, owner_user_id=7, name="models", endpoint="https://s3.example.test",
                bucket="triton-models", access_key="access", secret_key_enc="secret",
            ))
            session.commit()

    def _request(self, uri: str = "s3://triton-models/dev/iris_classifier") -> CreateDeploymentRequest:
        return CreateDeploymentRequest(
            deployment_name="iris-classifier", image="triton:test", s3_profile_id=11,
            model_uri=uri, repository_prefix="dev", model_name="iris_classifier",
        )

    def test_global_profile_resolves_for_all_users(self) -> None:
        with Session(self.engine) as session:
            resolved = profile_request.resolve_profile_request(
                self._request(), session, {"email": "owner@example.com", "auth_provider": "local"}
            )
            self.assertEqual(resolved.s3_url, "s3://https://s3.example.test/triton-models/dev")
            self.assertEqual(resolved.s3_access_key, "access")
            other = profile_request.resolve_profile_request(
                self._request(), session, {"email": "other@example.com", "auth_provider": "local"}
            )
            self.assertEqual(other.s3_access_key, "access")

    def test_model_uri_bucket_must_match_profile(self) -> None:
        with Session(self.engine) as session:
            with self.assertRaises(BadRequestError):
                profile_request.resolve_profile_request(
                    self._request("s3://another-bucket/dev/iris_classifier"),
                    session, {"email": "owner@example.com", "auth_provider": "local"},
                )

    def test_workflow_token_is_limited_to_profile_and_deployment(self) -> None:
        claims = {
            "email": "owner@example.com", "auth_provider": "local",
            "token_use": "mlflow_workflow", "allowed_s3_profile_id": 11,
            "allowed_deployment_name": "another-deployment",
        }
        with Session(self.engine) as session:
            with self.assertRaises(BadRequestError):
                profile_request.resolve_profile_request(self._request(), session, claims)

    def test_legacy_request_rejected_at_deployment_boundary(self) -> None:
        request = CreateDeploymentRequest(
            deployment_name="legacy", image="triton:test", s3_url="s3://bucket",
            s3_access_key="access", s3_secret_key="secret",
        )
        with Session(self.engine) as session:
            with self.assertRaises(BadRequestError):
                profile_request.resolve_profile_request(request, session, {})

    def test_workspace_token_uses_global_profile(self) -> None:
        claims = {"email": "owner@example.com", "auth_provider": "local", "token_use": "workspace_deployment"}
        with Session(self.engine) as session:
            resolved = profile_request.resolve_profile_request(self._request(), session, claims)
            self.assertEqual(resolved.s3_access_key, "access")
            other = profile_request.resolve_profile_request(
                self._request(), session, {**claims, "email": "other@example.com"},
            )
            self.assertEqual(other.s3_access_key, "access")
            with self.assertRaises(BadRequestError):
                profile_request.resolve_profile_request(CreateDeploymentRequest(
                    deployment_name="legacy", image="triton:test", s3_url="s3://bucket",
                    s3_access_key="access", s3_secret_key="secret",
                ), session, claims)

    def test_automatic_workflow_token_accepts_different_names_and_global_profiles(self):
        claims = {"email": "owner@example.com", "auth_provider": "local", "token_use": "workflow_deployment"}
        with Session(self.engine) as session:
            for name in ["first-model", "second-model"]:
                request = self._request().model_copy(update={"deployment_name": name})
                self.assertEqual(profile_request.resolve_profile_request(request, session, claims).deployment_name, name)
            other = profile_request.resolve_profile_request(
                self._request(), session, {**claims, "email": "other@example.com"},
            )
            self.assertEqual(other.s3_access_key, "access")
            with self.assertRaises(BadRequestError):
                profile_request.resolve_profile_request(self._request().model_copy(update={"s3_profile_id": None}), session, claims)

    def test_automatic_workflow_token_can_only_access_owner_deployments(self):
        claims = {"email": "owner@example.com", "auth_provider": "local", "token_use": "workflow_deployment"}
        with Session(self.engine) as session:
            for name, owner_id in [("first", 7), ("second", 7), ("foreign", 8)]:
                session.add(TritonInstanceEntity(name=name, url="http://triton.test", created_by_user_id=owner_id,
                    mlflow_model_uri="s3://models/repository/classifier"))
            session.commit()
            for name in ["first", "second"]:
                self.assertEqual(mlflow._owned(session, claims, name).name, name)
            with self.assertRaises(NotFoundError):
                mlflow._owned(session, claims, "foreign")


class MlflowReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_requires_selected_model_ready(self) -> None:
        row = TritonInstanceEntity(
            id=3, name="iris-classifier", url="http://triton.example", mlflow_model_name="iris_classifier",
            mlflow_model_uri="s3://bucket/dev/iris_classifier", created_by_user_id=7,
        )
        with patch("app.services.deployment.mlflow.TritonService") as service_cls:
            service = service_cls.return_value
            service.is_ready = AsyncMock(return_value=True)
            service.get_repository_index = AsyncMock(return_value=[{"name": "other", "state": "READY"}])
            self.assertEqual((await mlflow.describe(row))["status"], "starting")
            service.get_repository_index = AsyncMock(return_value=[{"name": "iris_classifier", "state": "READY"}])
            self.assertEqual((await mlflow.describe(row))["status"], "ready")

    async def test_managed_deployment_reports_external_url_without_credentials(self):
        # Arrange
        from app.schemas.deployment_access import DeploymentAccessResponse
        row = TritonInstanceEntity(
            id=4, name="model", url="http://internal:18000", is_self_deployed=True,
            mlflow_model_uri="s3://models/classifier", created_by_user_id=7,
        )
        exposure = DeploymentAccessResponse(
            access_mode="ingress", url="https://model.test", internal_url=row.url,
            ingress_host="model.test", ingress_scheme="https", basic_auth_enabled=True,
        )
        with patch("app.services.deployment.mlflow.access.read_instance_access", return_value=exposure):
            # Act
            result = await mlflow.describe(row)
        # Assert
        self.assertEqual(result["url"], row.url)
        self.assertEqual(result["internal_url"], row.url)
        self.assertEqual(result["external_url"], "https://model.test")
        self.assertTrue(result["access"]["basic_auth_enabled"])
        self.assertNotIn("basic_auth_password", result["access"])

    async def test_transport_failure_reports_unknown_exposure(self):
        from unittest.mock import Mock

        from urllib3.exceptions import MaxRetryError

        for operation in ("get", "list"):
            with self.subTest(operation=operation):
                # Arrange
                row = TritonInstanceEntity(
                    id=4, name="model", url="http://internal:18000", is_self_deployed=True,
                    deployment_name="model", deployment_namespace="control", deployment_service_name="model-service",
                    mlflow_model_uri="s3://models/classifier", created_by_user_id=7,
                )
                with (
                    patch("app.services.deployment.access.api_client"),
                    patch("app.services.deployment.access.client.NetworkingV1Api") as network,
                    patch.object(mlflow, "_owned", return_value=row),
                    patch.object(mlflow, "require_user_entity", return_value=Mock(id=7)),
                    patch.object(mlflow.instances, "list_mlflow_for_owner", return_value=[row]),
                ):
                    network.return_value.read_namespaced_ingress.side_effect = MaxRetryError(None, "/private")
                    # Act
                    results = ([await mlflow.get(Mock(), {}, "model")] if operation == "get"
                               else await mlflow.list_all(Mock(), {}))
                # Assert
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["url"], row.url)
                self.assertIsNone(results[0]["external_url"])
                self.assertIsNone(results[0]["access"])
                self.assertEqual(results[0]["access_error"], "Could not read endpoint configuration from Kubernetes")
