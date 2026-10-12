"""Creator attribution is enforced at the API boundary, including SDK writes."""

import base64
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.responses import Response

from app.api import mlflow_api
from app.core.security import get_claims
from app.db.entities import CodeServerEntity, UserEntity
from app.exceptions import BadRequestError, ForbiddenError, UnauthorizedError
from app.services.development import kubernetes as workspace_k8s
from app.services.mlflow import installer, proxy, tracking
from app.services.mlflow import kubernetes as mlflow_k8s


class CreatorEnforcementTests(unittest.TestCase):
    def setUp(self):
        self.claims = {"email": "owner@example.com", "role": "member"}

    def rewrite(self, action, payload, method="POST", prefix="api/"):
        return tracking.enforce_creator(
            prefix + "2.0/mlflow/" + action, method, json.dumps(payload).encode(), self.claims,
        )

    def test_create_overwrites_duplicate_tags_and_legacy_creator(self):
        for prefix in ("api/", "ajax-api/"):
            with self.subTest(prefix=prefix):
                body = json.loads(self.rewrite("runs/create", {
                    "experiment_id": "1", "user_id": "someone-else",
                    "tags": [
                        {"key": "mlflow.user", "value": "fake-1"},
                        {"key": "mlflow.user", "value": "fake-2"},
                        {"key": "mlflow.runName", "value": "training"},
                    ],
                }, prefix=prefix))
                self.assertEqual(body["user_id"], "owner@example.com")
                self.assertEqual(body["tags"], [
                    {"key": "mlflow.runName", "value": "training"},
                    {"key": "mlflow.user", "value": "owner@example.com"},
                ])

    def test_creator_edits_and_deletion_are_rejected(self):
        attempts = [
            ("runs/set-tag", {"key": "mlflow.user", "value": "fake"}, "POST"),
            ("runs/delete-tag", {"key": "mlflow.user"}, "POST"),
            ("runs/log-batch", {"tags": [{"key": "mlflow.user", "value": "fake"}]}, "POST"),
            ("logged-models/m-1/tags", {"tags": [{"key": "mlflow.user", "value": "fake"}]}, "PATCH"),
            ("logged-models/m-1/tags/mlflow.user", {}, "DELETE"),
        ]
        for action, payload, method in attempts:
            for prefix in ("api/", "ajax-api/"):
                with self.subTest(action=action, prefix=prefix):
                    with self.assertRaises(ForbiddenError):
                        self.rewrite(action, payload, method, prefix)

    def test_metrics_and_unrelated_tags_are_preserved(self):
        payload = {"run_id": "run-1", "metrics": [{"key": "accuracy", "value": 0.9}],
                   "tags": [{"key": "deployment_status", "value": "deployed"}]}
        self.assertEqual(json.loads(self.rewrite("runs/log-batch", payload)), payload)

    def test_logged_model_has_the_same_creator(self):
        result = json.loads(self.rewrite("logged-models", {"name": "checkpoint"}))
        self.assertEqual(result["tags"], [{"key": "mlflow.user", "value": "owner@example.com"}])
        self.assertNotIn("user_id", result)

    def test_model_ui_creator_fields_come_from_persisted_tags(self):
        payload: dict[str, Any] = {"models": [{"info": {
            "model_id": "m-1", "experiment_id": "1", "tags": [{"key": "mlflow.user", "value": "alice"}],
        }}]}
        response = Response(json.dumps(payload), media_type="application/json")
        updated = tracking.expose_creator("api/2.0/mlflow/logged-models/search", response)
        self.assertIs(updated, response)
        payload = {"registered_models": [{"name": "iris", "latest_versions": [{
            "name": "iris", "version": "3", "tags": [{"key": "mlflow.user", "value": "bob"}],
        }]}]}
        response = Response(json.dumps(payload), media_type="application/json")
        updated = tracking.expose_creator("ajax-api/2.0/mlflow/registered-models/search", response)
        self.assertEqual(json.loads(updated.body)["registered_models"][0]["latest_versions"][0]["user_id"], "bob")

    def test_registered_models_and_versions_record_the_authenticated_creator(self):
        for action in ("registered-models/create", "model-versions/create"):
            result = json.loads(self.rewrite(action, {"name": "iris", "tags": [
                {"key": "mlflow.user", "value": "fake"},
            ]}))
            self.assertEqual(result["tags"], [{"key": "mlflow.user", "value": "owner@example.com"}])
        with self.assertRaises(ForbiddenError):
            self.rewrite("model-versions/set-tag", {"key": "mlflow.user", "value": "fake"})

    def test_binary_artifact_upload_is_unchanged(self):
        body = b"\x00\xffmodel-bytes"
        self.assertEqual(tracking.enforce_creator(
            "api/2.0/mlflow-artifacts/artifacts/models/model.onnx", "PUT", body, self.claims,
        ), body)

    def test_graphql_reads_work_but_mutations_cannot_bypass_attribution(self):
        body = json.dumps({"query": "query { __typename }"}).encode()
        self.assertEqual(tracking.enforce_creator("graphql", "POST", body, self.claims), body)
        for query in ("mutation { createRun }", "query { __typename } mutation Attack { createRun }"):
            with self.assertRaises(ForbiddenError):
                tracking.enforce_creator("graphql", "POST", json.dumps({"query": query}).encode(), self.claims)

    def test_malformed_creator_requests_and_ambiguous_paths_fail_closed(self):
        for body in (b"null", b"[]", b'{"tags":null}', b'{"tags":[1]}'):
            with self.assertRaises(BadRequestError):
                tracking.enforce_creator("api/2.0/mlflow/runs/create", "POST", body, self.claims)
        for path in ("api//2.0/mlflow/runs/create", "api/2.0/mlflow/runs/../runs/create",
                     "api/2.0/mlflow/runs%2fcreate"):
            with self.assertRaises(BadRequestError):
                tracking.enforce_creator(path, "POST", b"{}", self.claims)

    def test_missing_identity_cannot_create_a_run(self):
        with self.assertRaises(UnauthorizedError):
            tracking.enforce_creator("api/2.0/mlflow/runs/create", "POST", b"{}", {})


class WorkloadIdentityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add(UserEntity(id=7, email="owner@example.com", name="Owner", role="member", is_active=True))
            session.add(CodeServerEntity(
                owner_user_id=7, name="workspace", namespace="control", statefulset_name="code-7-workspace",
                service_name="workspace-svc", secret_name="workspace-secret", image="python:3.12",
            ))
            session.commit()
        self.secret = SimpleNamespace(
            data={tracking.TRACKING_TOKEN_KEY: base64.b64encode(b"managed-token").decode()},
            metadata=SimpleNamespace(annotations={tracking.WORKFLOW_OWNER_ANNOTATION: "7"}),
        )

    def authenticate(self, kind, name, token="managed-token"):
        with patch.object(tracking, "session_factory", side_effect=lambda: Session(self.engine)), patch(
            "app.services.mlflow.tracking.api_client"
        ), patch("app.services.mlflow.tracking.client.CoreV1Api") as core:
            core.return_value.read_namespaced_secret.return_value = self.secret
            return tracking.authenticate_workload(kind, "control", name, token)

    def test_workspace_identity_comes_from_database_owner(self):
        # Even a misleading annotation on the workspace Secret cannot change its owner.
        self.secret.metadata.annotations[tracking.WORKFLOW_OWNER_ANNOTATION] = "999"
        self.assertEqual(self.authenticate("workspaces", "code-7-workspace")["email"], "owner@example.com")

    def test_workflow_identity_comes_from_managed_secret(self):
        self.assertEqual(self.authenticate("workflows", "workflow-secret")["email"], "owner@example.com")

    def test_invalid_token_and_wrong_workspace_are_rejected(self):
        for token in ("", "someone-elses-token", "x" * 257):
            with self.assertRaises(UnauthorizedError):
                self.authenticate("workspaces", "code-7-workspace", token)
        with self.assertRaises(UnauthorizedError):
            self.authenticate("workspaces", "someone-elses-workspace")

    def test_disabled_owner_and_deleted_credentials_are_rejected(self):
        with Session(self.engine) as session:
            owner = session.get(UserEntity, 7)
            owner.is_active = False
            session.add(owner)
            session.commit()
        with self.assertRaises(ForbiddenError):
            self.authenticate("workspaces", "code-7-workspace")
        with patch.object(tracking, "session_factory", side_effect=lambda: Session(self.engine)), patch(
            "app.services.mlflow.tracking.api_client"
        ), patch("app.services.mlflow.tracking.client.CoreV1Api") as core:
            core.return_value.read_namespaced_secret.side_effect = ApiException(status=404)
            with self.assertRaises(UnauthorizedError):
                tracking.authenticate_workload("workflows", "control", "deleted-secret", "managed-token")


class TrackingApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(mlflow_api.router)
        app.dependency_overrides[get_claims] = lambda: {"email": "owner@example.com", "role": "member"}
        self.client = TestClient(app)

    def test_browser_and_sdk_have_identical_creator_enforcement(self):
        for base in ("/api/mlflow/proxy", "/api/mlflow/tracking/workflows/control/workflow-secret"):
            with self.subTest(base=base), patch.object(
                tracking, "authenticate_workload", return_value={"email": "owner@example.com", "role": "member"}
            ), patch.object(proxy, "_proxy_server_url", return_value="http://mlflow.control:5000"), patch.object(
                proxy, "_proxy_http_sync", return_value=Response(b"{}", media_type="application/json")
            ) as upstream:
                response = self.client.post(base + "/api/2.0/mlflow/runs/create", headers={
                    "Authorization": "Bearer managed-token", "X-Triton-Mlflow-Gateway": "fake-gateway-token",
                }, json={"user_id": "fake", "tags": [{"key": "mlflow.user", "value": "fake"}]})
                self.assertEqual(response.status_code, 200)
                args = upstream.call_args.args
                self.assertNotIn("authorization", args[3])
                self.assertNotIn("x-triton-mlflow-gateway", args[3])
                self.assertEqual(json.loads(args[-1])["tags"], [{"key": "mlflow.user", "value": "owner@example.com"}])
                denied = self.client.post(base + "/api/2.0/mlflow/runs/set-tag", headers={
                    "Authorization": "Bearer managed-token",
                }, json={"key": "mlflow.user", "value": "fake"})
                self.assertEqual(denied.status_code, 403)
                self.assertEqual(upstream.call_count, 1)

    def test_sdk_requires_workload_credential_not_browser_cookie(self):
        with patch.object(proxy, "proxy_http", AsyncMock()) as upstream:
            response = self.client.post(
                "/api/mlflow/tracking/workspaces/control/code-7-workspace/api/2.0/mlflow/runs/create", json={},
            )
        self.assertEqual(response.status_code, 401)
        upstream.assert_not_called()

    def test_non_admin_cannot_upgrade_other_users_workspaces(self):
        with patch.object(installer, "upgrade_mlflow_tracking") as upgrade:
            response = self.client.post("/api/mlflow/upgrade")
        self.assertEqual(response.status_code, 403)
        upgrade.assert_not_called()


class ManagedInstallationTests(unittest.TestCase):
    def test_upgrade_reuses_gateway_and_registry_credentials(self):
        pull = base64.b64encode(b'{"auths":{"private.example":{"auth":"fixture"}}}').decode()
        with patch.object(mlflow_k8s, "_client"), patch("kubernetes.client.CoreV1Api") as core, patch.object(
            mlflow_k8s, "apply_installation_resources", return_value=["Deployment/mlflow"]
        ) as apply, patch.object(mlflow_k8s, "_wait_for_current_deployment"):
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={".dockerconfigjson": pull})
            mlflow_k8s.upgrade_installation_resources("control", "mlflow", "mlflow-service")
            self.assertEqual(apply.call_args.args[0].dockerconfigjson,
                             '{"auths":{"private.example":{"auth":"fixture"}}}')
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={
                "gateway-token": base64.b64encode(b"existing-credential").decode(),
            })
            self.assertEqual(mlflow_k8s._gateway_token(object(), "control", "mlflow"), "existing-credential")

    def test_upgrade_waits_for_old_unprotected_pods_to_leave_the_service(self):
        old = SimpleNamespace(
            spec=SimpleNamespace(replicas=1), metadata=SimpleNamespace(generation=2),
            status=SimpleNamespace(observed_generation=1, updated_replicas=0, ready_replicas=1, replicas=1),
        )
        current = SimpleNamespace(
            spec=SimpleNamespace(replicas=1), metadata=SimpleNamespace(generation=2),
            status=SimpleNamespace(observed_generation=2, updated_replicas=1, ready_replicas=1, replicas=1),
        )
        with patch("kubernetes.client.AppsV1Api") as apps, patch.object(mlflow_k8s.time, "sleep") as sleep:
            apps.return_value.read_namespaced_deployment.side_effect = [old, current]
            mlflow_k8s._wait_for_current_deployment(object(), "control", "mlflow")
        sleep.assert_called_once()

    def test_gateway_blocks_direct_access_and_server_is_loopback_only(self):
        from app.schemas import InstallMlflowRequest

        manifests = mlflow_k8s._manifests(
            InstallMlflowRequest(installation_name="mlflow"), "control", "mlflow", "mlflow-service",
            gateway_token="test-upstream-credential",
        )
        secret = next(item for item in manifests if item["kind"] == "Secret")
        self.assertIn('if ($http_x_triton_mlflow_gateway != "test-upstream-credential") { return 401; }',
                      secret["stringData"]["nginx.conf"])
        pod = next(item for item in manifests if item["kind"] == "Deployment")["spec"]["template"]["spec"]
        self.assertIn("--host 127.0.0.1", pod["containers"][0]["args"][0])
        self.assertIn("--port 5001", pod["containers"][0]["args"][0])
        self.assertEqual(pod["containers"][1]["ports"][0]["containerPort"], 5000)
        self.assertFalse(pod["automountServiceAccountToken"])

    def test_existing_workspace_upgrade_preserves_secrets_and_storage(self):
        with patch.object(workspace_k8s, "api_client"), patch("kubernetes.client.CoreV1Api") as core, patch(
            "kubernetes.client.AppsV1Api"
        ) as apps:
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={"S3_PROFILE_TOKEN": "keep"})
            workspace_k8s.enable_mlflow_tracking("control", "code-7", "workspace-secret")
            patch_body = core.return_value.patch_namespaced_secret.call_args.args[2]
            self.assertEqual(set(patch_body["stringData"]), {tracking.TRACKING_TOKEN_KEY})
            pod_patch = apps.return_value.patch_namespaced_stateful_set.call_args.args[2]
            self.assertNotIn("volumeClaimTemplates", pod_patch["spec"])
            env = pod_patch["spec"]["template"]["spec"]["containers"][0]["env"]
            self.assertEqual(env[0]["name"], "MLFLOW_TRACKING_URI")
            core.return_value.read_namespaced_secret.return_value.data[tracking.TRACKING_TOKEN_KEY] = "already-set"
            core.return_value.patch_namespaced_secret.reset_mock()
            workspace_k8s.enable_mlflow_tracking("control", "code-7", "workspace-secret")
            core.return_value.patch_namespaced_secret.assert_not_called()
