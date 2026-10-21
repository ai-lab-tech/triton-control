"""Training workflows resolve owned profiles before managed tracking injection."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.entities import S3ProfileEntity, UserEntity, WorkflowS3CredentialEntity
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.services.workflows import mlflow_delegation
from app.services.workflows.artifact_repository import repository_data


class TrainingS3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member"))
            session.add(UserEntity(id=8, email="other@example.com", name="Other", role="member"))
            session.add(
                S3ProfileEntity(
                    id=11,
                    owner_user_id=7,
                    name="training",
                    endpoint="https://s3.example.test",
                    bucket="training-models",
                    access_key="access",
                    secret_key_enc="encrypted",
                    ca_certificate="public-ca",
                    region="eu-central-1",
                )
            )
            session.add(
                WorkflowS3CredentialEntity(
                    id=21,
                    created_by_user_id=7,
                    name="training",
                    namespace="triton-control",
                    secret_name="workflow-s3-training",
                    access_key_id="access",
                    s3_profile_id=11,
                    s3_profile_name="training",
                )
            )
            session.commit()
        self.workflow = {
            "metadata": {
                "generateName": "example-training-",
                "annotations": {
                    "triton-control.ai/mlflow-s3-profile-name": "training",
                },
            },
            "spec": {
                "templates": [
                    {"name": "train", "container": {"image": "python:3.12"}},
                    {"name": "script", "script": {"image": "python:3.12", "source": "pass"}},
                ]
            },
        }

    def tearDown(self) -> None:
        self.engine.dispose()

    def submit(self, email: str = "owner@example.com") -> tuple[dict, object]:
        with Session(self.engine) as session:
            profile = session.get(S3ProfileEntity, 11)
            repository = SimpleNamespace(data={"repository": repository_data(profile, "workflow-s3-training")})
        with (
            patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)),
            patch.object(mlflow_delegation, "api_client"),
            patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core,
        ):
            core.return_value.read_namespaced_config_map.return_value = repository
            body, _, _ = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control",
                json.dumps({"workflow": self.workflow}).encode(),
                {"email": email, "auth_provider": "local", "role": "member"},
            )
        return json.loads(body)["workflow"], core.return_value

    def test_named_profile_configures_training_containers_and_ca(self) -> None:
        workflow, core = self.submit()
        spec = workflow["spec"]
        self.assertEqual(spec["artifactRepositoryRef"], {"configMap": "workflow-s3-training", "key": "repository"})
        self.assertEqual(spec["volumes"][0]["secret"]["secretName"], "workflow-s3-training")
        for template in spec["templates"]:
            pod = template.get("container") or template["script"]
            env = {value["name"]: value for value in pod["env"]}
            self.assertEqual(env["S3_ENDPOINT_URL"]["value"], "https://s3.example.test")
            self.assertEqual(env["S3_BUCKET"]["value"], "training-models")
            self.assertEqual(env["AWS_DEFAULT_REGION"]["value"], "eu-central-1")
            self.assertEqual(env["AWS_ACCESS_KEY_ID"]["valueFrom"]["secretKeyRef"]["name"], "workflow-s3-training")
            self.assertEqual(env["AWS_SECRET_ACCESS_KEY"]["valueFrom"]["secretKeyRef"]["key"], "secret-access-key")
            self.assertEqual(env["TRITON_CONTROL_S3_PROFILE_ID"]["value"], "11")
            self.assertIn("AWS_CA_BUNDLE", env)
            self.assertIn("MLFLOW_TRACKING_TOKEN", env)
            self.assertTrue(pod["volumeMounts"][0]["readOnly"])
            self.assertNotIn("TRITON_CONTROL_TOKEN", env)
        self.assertNotIn("public-ca", json.dumps(workflow))
        self.assertNotIn("encrypted", json.dumps(workflow))
        core.create_namespaced_secret.assert_called_once()  # Tracking credential only.

    def test_id_profile_without_ca_honors_virtual_addressing(self) -> None:
        self.workflow["metadata"]["annotations"] = {"triton-control.ai/mlflow-s3-profile-id": "11"}
        with Session(self.engine) as session:
            profile = session.get(S3ProfileEntity, 11)
            profile.ca_certificate = ""
            profile.force_path_style = False
            session.add(profile)
            session.commit()
        workflow, _ = self.submit()
        env = {value["name"]: value for value in workflow["spec"]["templates"][0]["container"]["env"]}
        self.assertNotIn("AWS_CA_BUNDLE", env)
        self.assertEqual(workflow["spec"]["volumes"], [])
        self.assertEqual(env["S3_ADDRESSING_STYLE"]["value"], "virtual")

    def test_foreign_profile_is_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            self.submit("other@example.com")

    def test_profile_must_be_linked_in_submission_namespace(self) -> None:
        with Session(self.engine) as session:
            row = session.get(WorkflowS3CredentialEntity, 21)
            row.namespace = "another-namespace"
            session.add(row)
            session.commit()
        with self.assertRaises(BadRequestError):
            self.submit()

    def test_ambiguous_profile_is_rejected(self) -> None:
        self.workflow["metadata"]["annotations"]["triton-control.ai/mlflow-s3-profile-id"] = "11"
        with self.assertRaises(BadRequestError):
            self.submit()

    def test_conflicting_repository_is_rejected(self) -> None:
        self.workflow["spec"]["artifactRepositoryRef"] = {"configMap": "other-repository", "key": "repository"}
        with self.assertRaises(BadRequestError):
            self.submit()

    def test_explicit_connection_environment_is_rejected(self) -> None:
        self.workflow["spec"]["templates"][0]["container"]["env"] = [{"name": "S3_BUCKET", "value": "other"}]
        with self.assertRaises(BadRequestError):
            self.submit()

    def test_user_supplied_foreign_secret_is_still_rejected(self) -> None:
        self.workflow["spec"]["volumes"] = [{"name": "foreign", "secret": {"secretName": "foreign"}}]
        with self.assertRaises(ForbiddenError):
            self.submit()
