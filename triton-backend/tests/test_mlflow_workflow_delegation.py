"""Argo submission injects a user-scoped token without exposing it in YAML."""

import json
import unittest
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.user_auth import verify_access_token
from app.db.entities import S3ProfileEntity, UserEntity
from app.exceptions import BadRequestError, NotFoundError
from app.services.workflows import mlflow_delegation


class WorkflowDelegationTests(unittest.TestCase):
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

    def _submission(self) -> bytes:
        return json.dumps({"workflow": {
            "metadata": {"name": "train-iris", "annotations": {
                "triton-control.ai/mlflow-deploy-template": "deploy",
                "triton-control.ai/mlflow-deployment-name": "iris-classifier",
                "triton-control.ai/mlflow-s3-profile-name": "models",
            }},
            "spec": {"templates": [{"name": "deploy", "container": {"image": "python:3.12"}}]},
        }}).encode()

    def test_injects_secret_reference_and_scoped_token(self) -> None:
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.in_cluster_namespace", return_value="triton-control"),
            patch("app.services.workflows.mlflow_delegation.api_client"),
            patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core_api,
        ):
            body, secret_name, namespace = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", self._submission(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )
        workflow = json.loads(body)["workflow"]
        env = {item["name"]: item for item in workflow["spec"]["templates"][0]["container"]["env"]}
        self.assertEqual(env["TRITON_CONTROL_TOKEN"]["valueFrom"]["secretKeyRef"]["name"], secret_name)
        self.assertEqual(env["TRITON_CONTROL_S3_PROFILE_ID"]["value"], "11")
        self.assertEqual(namespace, "triton-control")
        self.assertEqual(workflow["spec"]["ttlStrategy"]["secondsAfterCompletion"], 300)
        secret = core_api.return_value.create_namespaced_secret.call_args.kwargs["body"]
        claims = verify_access_token(secret.string_data["token"])
        self.assertEqual(claims["allowed_s3_profile_id"], 11)
        self.assertEqual(claims["allowed_deployment_name"], "iris-classifier")
        self.assertNotIn(secret.string_data["token"], body.decode())

    def test_legacy_profile_id_annotation_still_works(self) -> None:
        submission = json.loads(self._submission())
        annotations = submission["workflow"]["metadata"]["annotations"]
        del annotations["triton-control.ai/mlflow-s3-profile-name"]
        annotations["triton-control.ai/mlflow-s3-profile-id"] = "11"
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.in_cluster_namespace", return_value="triton-control"),
            patch("app.services.workflows.mlflow_delegation.api_client"),
            patch("app.services.workflows.mlflow_delegation.client.CoreV1Api"),
        ):
            body, _, _ = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )
        env = json.loads(body)["workflow"]["spec"]["templates"][0]["container"]["env"]
        self.assertIn({"name": "TRITON_CONTROL_S3_PROFILE_ID", "value": "11"}, env)

    def test_rejects_ambiguous_profile_reference(self) -> None:
        submission = json.loads(self._submission())
        submission["workflow"]["metadata"]["annotations"]["triton-control.ai/mlflow-s3-profile-id"] = "11"
        with patch("app.services.workflows.mlflow_delegation.in_cluster_namespace", return_value="triton-control"):
            with self.assertRaises(BadRequestError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
                )

    def test_rejects_workflow_supplied_profile_id_environment(self) -> None:
        submission = json.loads(self._submission())
        submission["workflow"]["spec"]["templates"][0]["container"]["env"] = [{
            "name": "TRITON_CONTROL_S3_PROFILE_ID", "value": "99",
        }]
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.in_cluster_namespace", return_value="triton-control"),
        ):
            with self.assertRaises(BadRequestError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
                )

    def test_other_user_cannot_select_profile(self) -> None:
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.in_cluster_namespace", return_value="triton-control"),
        ):
            with self.assertRaises(NotFoundError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", self._submission(),
                    {"email": "other@example.com", "auth_provider": "local", "role": "member"},
                )
