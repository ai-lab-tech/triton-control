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

    def test_profile_resolves_only_for_owner(self) -> None:
        with Session(self.engine) as session:
            resolved = profile_request.resolve_profile_request(
                self._request(), session, {"email": "owner@example.com", "auth_provider": "local"}
            )
            self.assertEqual(resolved.s3_url, "s3://https://s3.example.test/triton-models/dev")
            self.assertEqual(resolved.s3_access_key, "access")
            with self.assertRaises(NotFoundError):
                profile_request.resolve_profile_request(
                    self._request(), session, {"email": "other@example.com", "auth_provider": "local"}
                )

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

    def test_legacy_request_still_valid(self) -> None:
        request = CreateDeploymentRequest(
            deployment_name="legacy", image="triton:test", s3_url="s3://bucket",
            s3_access_key="access", s3_secret_key="secret",
        )
        with Session(self.engine) as session:
            self.assertIs(profile_request.resolve_profile_request(request, session, {}), request)


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
