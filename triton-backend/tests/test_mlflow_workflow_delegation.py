"""Argo submission injects a user-scoped token without exposing it in YAML."""

import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.user_auth import verify_access_token
from app.db.entities import S3ProfileEntity, UserEntity, WorkflowS3CredentialEntity
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.services.workflows import mlflow_delegation


class WorkflowDelegationTests(unittest.TestCase):
    def test_plain_workflow_can_use_owned_repository(self) -> None:
        # Arrange
        reference = {"configMap": "workflow-s3-models", "key": "repository"}
        submission = {"workflow": {"spec": {"artifactRepositoryRef": reference, "templates": []}}}
        repository = SimpleNamespace(data={"repository": json.dumps({"s3": {
            "accessKeySecret": {"name": "workflow-s3-models", "key": "access-key-id"},
            "secretKeySecret": {"name": "workflow-s3-models", "key": "secret-access-key"},
        }})})
        with patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)), patch.object(
            mlflow_delegation, "api_client"
        ), patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core:
            core.return_value.read_namespaced_config_map.return_value = repository

            # Act
            body, _, _ = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )

        # Assert
        self.assertEqual(json.loads(body)["workflow"]["spec"]["artifactRepositoryRef"], reference)
        core.return_value.create_namespaced_secret.assert_called_once()

    def test_plain_workflow_cannot_select_other_users_repository(self) -> None:
        # Arrange
        submission = {"workflow": {"spec": {"artifactRepositoryRef": {
            "configMap": "workflow-s3-models", "key": "repository",
        }, "templates": []}}}

        # Act / Assert
        with patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)), patch(
            "app.services.workflows.mlflow_delegation.client.CoreV1Api"
        ) as core:
            with self.assertRaises(ForbiddenError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": "other@example.com", "auth_provider": "local", "role": "member"},
                )
        core.assert_not_called()

    def test_owned_repository_cannot_resolve_foreign_secret(self) -> None:
        # Arrange
        submission = {"workflow": {"spec": {"artifactRepositoryRef": {
            "configMap": "workflow-s3-models", "key": "repository",
        }, "templates": []}}}
        repository = SimpleNamespace(data={"repository": json.dumps({"s3": {
            "accessKeySecret": {"name": "foreign-secret", "key": "access-key-id"},
        }})})

        # Act / Assert
        with patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)), patch.object(
            mlflow_delegation, "api_client"
        ), patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core:
            core.return_value.read_namespaced_config_map.return_value = repository
            with self.assertRaises(ForbiddenError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
                )
        core.return_value.create_namespaced_secret.assert_not_called()

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member"))
            session.add(UserEntity(id=8, email="other@example.com", name="Other", role="member"))
            session.add(S3ProfileEntity(
                id=11, owner_user_id=7, name="models", endpoint="https://s3.example.test",
                bucket="triton-models", access_key="access", secret_key_enc="secret",
            ))
            session.add(WorkflowS3CredentialEntity(
                id=21, created_by_user_id=7, name="models", namespace="triton-control",
                secret_name="workflow-s3-models", access_key_id="access",
                s3_profile_id=11, s3_profile_name="models",
            ))
            session.commit()

    def _submission(self) -> bytes:
        return json.dumps({"workflow": {
            "metadata": {"name": "train-iris", "annotations": {
                "triton-control.ai/mlflow-s3-profile-name": "models",
            }},
            "spec": {"templates": [{"name": "deploy", "inputs": {"artifacts": [{
                "name": "script", "path": "/tmp/train.py", "s3": {"key": "train.py"},
            }]}, "container": {"image": "python:3.12"}}]},
        }}).encode()

    def test_injects_secret_reference_and_scoped_token(self) -> None:
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.api_client"),
            patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core_api,
        ):
            core_api.return_value.read_namespaced_config_map.return_value = self._repository()
            body, secret_name, namespace = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", self._submission(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )
        workflow = json.loads(body)["workflow"]
        env = {item["name"]: item for item in workflow["spec"]["templates"][0]["container"]["env"]}
        self.assertEqual(env["TRITON_CONTROL_TOKEN"]["valueFrom"]["secretKeyRef"]["name"], secret_name)
        self.assertEqual(env["TRITON_CONTROL_S3_PROFILE_NAME"]["value"], "models")
        self.assertEqual(namespace, "triton-control")
        secret = core_api.return_value.create_namespaced_secret.call_args.kwargs["body"]
        claims = verify_access_token(secret.string_data["token"])
        self.assertEqual(claims["token_use"], "workflow_deployment")
        self.assertNotIn("allowed_s3_profile_id", claims)
        self.assertNotIn("allowed_deployment_name", claims)
        self.assertNotIn(secret.string_data["token"], body.decode())
        self.assertIn("MLFLOW_TRACKING_TOKEN", env)
        self.assertIn(f"/tracking/workflows/{namespace}/{secret_name}", env["MLFLOW_TRACKING_URI"]["value"])
        self.assertEqual(secret.metadata.annotations["triton-control.ai/mlflow-owner-user-id"], "7")
        self.assertNotIn(secret.string_data["MLFLOW_TRACKING_TOKEN"], body.decode())

    def test_plain_workflow_gets_submitter_identity_in_every_training_container(self) -> None:
        submission = {"workflow": {
            "metadata": {"generateName": "training-"},
            "spec": {"templates": [
                {"name": "train", "container": {"image": "python:3.12", "env": [
                    {"name": "MLFLOW_TRACKING_URI", "value": "http://mlflow-service:5000"},
                    {"name": "MLFLOW_TRACKING_TOKEN", "value": "fake-token"},
                    {"name": "TRITON_CONTROL_URL", "value": "http://wrong-service:8000"},
                    {"name": "KEEP", "value": "yes"},
                ]}},
                {"name": "script", "script": {"image": "python:3.12", "source": "pass"}},
            ]},
        }}
        with patch.dict("os.environ", {"DEVELOPMENT_PROFILE_API_URL": "http://custom-control.custom-namespace:9000/"}), patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)), patch.object(
            mlflow_delegation, "api_client"
        ), patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core:
            body, secret_name, namespace = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )
        result = json.loads(body)["workflow"]
        for template in result["spec"]["templates"]:
            env = (template.get("script") or template.get("container"))["env"]
            self.assertEqual(sum(item["name"] == "MLFLOW_TRACKING_TOKEN" for item in env), 1)
            self.assertEqual(sum(item["name"] == "MLFLOW_TRACKING_URI" for item in env), 1)
            self.assertEqual(
                [item for item in env if item["name"] == "TRITON_CONTROL_URL"],
                [{"name": "TRITON_CONTROL_URL", "value": "http://custom-control.custom-namespace:9000"}],
            )
            self.assertNotIn("fake-token", json.dumps(env))
            self.assertEqual(sum(item["name"] == "TRITON_CONTROL_TOKEN" for item in env), 1)
        core.return_value.patch_namespaced_secret.assert_not_called()
        secret = core.return_value.create_namespaced_secret.call_args.kwargs["body"]
        self.assertEqual(secret.metadata.annotations["triton-control.ai/mlflow-owner-user-id"], "7")
        self.assertNotIn(secret.string_data["MLFLOW_TRACKING_TOKEN"], body.decode())
        self.assertEqual(namespace, "triton-control")
        self.assertTrue(secret_name.startswith("mlflow-training-"))

    def test_legacy_profile_id_annotation_still_works(self) -> None:
        submission = json.loads(self._submission())
        annotations = submission["workflow"]["metadata"]["annotations"]
        del annotations["triton-control.ai/mlflow-s3-profile-name"]
        annotations["triton-control.ai/mlflow-s3-profile-id"] = "11"
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
            patch("app.services.workflows.mlflow_delegation.api_client"),
            patch("app.services.workflows.mlflow_delegation.client.CoreV1Api") as core,
        ):
            core.return_value.read_namespaced_config_map.return_value = self._repository()
            body, _, _ = mlflow_delegation.prepare_submission(
                "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
            )
        env = json.loads(body)["workflow"]["spec"]["templates"][0]["container"]["env"]
        self.assertIn({"name": "TRITON_CONTROL_S3_PROFILE_NAME", "value": "models"}, env)

    def test_rejects_ambiguous_profile_reference(self) -> None:
        submission = json.loads(self._submission())
        submission["workflow"]["metadata"]["annotations"]["triton-control.ai/mlflow-s3-profile-id"] = "11"
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
        ):
            with self.assertRaises(BadRequestError):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": "owner@example.com", "auth_provider": "local", "role": "member"},
                )

    def test_managed_deployment_token_overwrites_workflow_value(self):
        submission = json.loads(self._submission())
        submission["workflow"]["spec"]["templates"][0]["container"]["env"] = [{
            "name": "TRITON_CONTROL_TOKEN", "value": "fake-token",
        }]
        result = self._submit(submission)
        env = result["spec"]["templates"][0]["container"]["env"]
        tokens = [item for item in env if item["name"] == "TRITON_CONTROL_TOKEN"]
        self.assertEqual(len(tokens), 1)
        self.assertIn("valueFrom", tokens[0])
        self.assertNotIn("fake-token", json.dumps(env))

    def test_other_user_must_link_global_profile_before_submitting(self) -> None:
        with (
            patch("app.services.workflows.mlflow_delegation.session_factory", side_effect=lambda: Session(self.engine)),
        ):
            with self.assertRaisesRegex(BadRequestError, "Link an S3 profile"):
                mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", self._submission(),
                    {"email": "other@example.com", "auth_provider": "local", "role": "member"},
                )

    def _repository(self, secret_name="workflow-s3-models"):
        return SimpleNamespace(data={"repository": json.dumps({"s3": {
            "accessKeySecret": {"name": secret_name, "key": "access-key-id"},
            "secretKeySecret": {"name": secret_name, "key": "secret-access-key"},
        }})})

    def _plain_s3_submission(self):
        return {"workflow": {"metadata": {"generateName": "train-"}, "spec": {
            "templates": [{"name": "train", "inputs": {"artifacts": [{
                "name": "script", "path": "/tmp/train.py", "s3": {"key": "train.py"},
            }]}, "container": {"image": "python:3.12"}}],
        }}}

    def _submit(self, submission, email="owner@example.com", secret="workflow-s3-models"):
        with patch.object(mlflow_delegation, "session_factory", side_effect=lambda: Session(self.engine)), patch.object(mlflow_delegation, "api_client"), patch.object(mlflow_delegation.client, "CoreV1Api") as core:
            core.return_value.read_namespaced_config_map.return_value = self._repository(secret)
            try:
                body, _, _ = mlflow_delegation.prepare_submission(
                    "api/v1/workflows/triton-control", json.dumps(submission).encode(),
                    {"email": email, "auth_provider": "local", "role": "member"},
                )
            except Exception:
                core.return_value.create_namespaced_secret.assert_not_called()
                raise
        return json.loads(body)["workflow"]

    def _add_linked_profile(self, owner_id=7, namespace="triton-control"):
        with Session(self.engine) as session:
            session.add(S3ProfileEntity(id=12, owner_user_id=owner_id, name="second",
                endpoint="https://s3.example.test", bucket="second", access_key="access"))
            session.add(WorkflowS3CredentialEntity(id=22, created_by_user_id=owner_id,
                name="second", namespace=namespace, secret_name="workflow-s3-second", access_key_id="access",
                s3_profile_id=12, s3_profile_name="second"))
            session.commit()

    def test_key_only_s3_artifact_selects_only_owned_linked_profile(self):
        self._add_linked_profile(owner_id=8)
        result = self._submit(self._plain_s3_submission())
        self.assertEqual(result["spec"]["artifactRepositoryRef"], {
            "configMap": "workflow-s3-models", "key": "repository",
        })

    def test_explicit_profile_name_selects_between_multiple_links(self):
        self._add_linked_profile()
        submission = self._plain_s3_submission()
        submission["workflow"]["metadata"]["annotations"] = {
            "triton-control.ai/mlflow-s3-profile-name": "second",
        }
        result = self._submit(submission, secret="workflow-s3-second")
        self.assertEqual(result["spec"]["artifactRepositoryRef"]["configMap"], "workflow-s3-second")

    def test_profile_name_resolves_global_profile_with_owned_workflow_secret(self):
        self._add_linked_profile()
        with Session(self.engine) as session:
            profile = session.get(S3ProfileEntity, 12)
            profile.owner_user_id = 8
            session.add(profile)
            session.commit()
        submission = self._plain_s3_submission()
        submission["workflow"]["metadata"]["annotations"] = {
            "triton-control.ai/mlflow-s3-profile-name": "second",
        }
        result = self._submit(submission, secret="workflow-s3-second")
        self.assertEqual(result["spec"]["artifactRepositoryRef"]["configMap"], "workflow-s3-second")

    def test_ambiguous_links_require_profile_selection(self):
        self._add_linked_profile()
        with self.assertRaisesRegex(BadRequestError, "Multiple linked S3 repositories"):
            self._submit(self._plain_s3_submission())

    def test_missing_link_does_not_use_another_users_repository(self):
        with self.assertRaisesRegex(BadRequestError, "Link an S3 profile"):
            self._submit(self._plain_s3_submission(), email="other@example.com")

    def test_links_in_other_namespaces_do_not_make_selection_ambiguous(self):
        self._add_linked_profile(namespace="another-namespace")
        result = self._submit(self._plain_s3_submission())
        self.assertEqual(result["spec"]["artifactRepositoryRef"]["configMap"], "workflow-s3-models")

    def test_deployment_uses_only_linked_profile_when_annotation_omitted(self):
        submission = json.loads(self._submission())
        del submission["workflow"]["metadata"]["annotations"]["triton-control.ai/mlflow-s3-profile-name"]
        result = self._submit(submission)
        env = result["spec"]["templates"][0]["container"]["env"]
        self.assertIn({"name": "TRITON_CONTROL_S3_PROFILE_NAME", "value": "models"}, env)
        self.assertEqual(result["spec"]["artifactRepositoryRef"]["configMap"], "workflow-s3-models")

    def test_ordinary_workflow_without_s3_needs_no_selection(self):
        self._add_linked_profile()
        result = self._submit({"workflow": {"metadata": {"generateName": "plain-"}, "spec": {
            "templates": [{"name": "train", "container": {"image": "python:3.12"}}],
        }}})
        self.assertNotIn("artifactRepositoryRef", result["spec"])

    def test_auto_selected_repository_cannot_reference_foreign_secret(self):
        with self.assertRaises(ForbiddenError):
            self._submit(self._plain_s3_submission(), secret="foreign-secret")

    def test_explicit_repository_is_preserved_with_multiple_links(self):
        self._add_linked_profile()
        submission = self._plain_s3_submission()
        submission["workflow"]["spec"]["artifactRepositoryRef"] = {
            "configMap": "workflow-s3-models", "key": "repository",
        }
        result = self._submit(submission)
        self.assertEqual(result["spec"]["artifactRepositoryRef"]["configMap"], "workflow-s3-models")

    def test_inline_s3_storage_does_not_need_default_repository(self):
        self._add_linked_profile()
        submission = self._plain_s3_submission()
        storage = submission["workflow"]["spec"]["templates"][0]["inputs"]["artifacts"][0]["s3"]
        storage.update({"bucket": "explicit", "endpoint": "s3.example.test"})
        self.assertNotIn("artifactRepositoryRef", self._submit(submission)["spec"])

    def test_unknown_profile_name_cannot_fall_back_to_another_profile(self):
        submission = self._plain_s3_submission()
        submission["workflow"]["metadata"]["annotations"] = {
            "triton-control.ai/mlflow-s3-profile-name": "missing",
        }
        with self.assertRaises(NotFoundError):
            self._submit(submission)
