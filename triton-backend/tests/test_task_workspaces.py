"""Task authorization, durable retries, pinned inputs and workspace isolation."""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.task_workspaces_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import CodeServerEntity, S3ProfileEntity, TaskWorkspaceEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, ServiceUnavailableError
from app.repositories import task_workspaces as repository
from app.schemas.preparation import ReasonRequest, TaskRevision
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import handoff_sources, preparation, task_workspaces, teams
from app.services import task_workspace_access as access
from app.services.development import kubernetes as k8s
from app.services.development import workspaces
from app.services.development.task_stage_runner import stage
from app.services.development.task_staging import configure
from tests.test_handoffs import HandoffFixture


class TaskWorkspaceTests(HandoffFixture, unittest.TestCase):
    def seed(self):
        super().seed()
        self.enterContext(patch.object(workspaces, "_require_kubernetes_enabled"))
        self.apply = self.enterContext(patch.object(
            k8s, "apply_code_server_resources", return_value=["StatefulSet/task"],
        ))
        self.enterContext(patch.object(task_workspaces, "apply_registry_settings", side_effect=lambda req, _: req))
        self.receipt = self.submit(self.request(
            dataset={"profile_id": 1, "path": "dataset.json"},
            baseline={"profile_id": 1, "path": "baseline.json"},
            expected_behavior="Predict iris class", acceptance_criteria="Accuracy >= 0.95",
        ))
        self.task = self.claim(self.receipt.task)

    def open(self, claims=None, request=None):
        return task_workspaces.open_workspace(self.session, claims or self.engineer, self.task.id,
                                              request or OpenTaskWorkspace())

    def row(self, result):
        return self.session.get(CodeServerEntity, result.workspace.id)

    def test_retry_reuses_one_association_and_kubernetes_workload(self):
        first, second = self.open(), self.open()
        self.assertEqual(first.workspace.id, second.workspace.id)
        self.assertEqual(len(self.session.exec(select(TaskWorkspaceEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(CodeServerEntity)).all()), 1)
        self.apply.assert_called_once()
        history = preparation.list_events(self.session, self.engineer, self.task.id)
        self.assertEqual(sum(item.action == "workspace_requested" for item in history), 1)

    def test_missing_inputs_block_start(self):
        incomplete = self.submit(self.request(revision="incomplete"))
        self.task = self.claim(incomplete.task)
        with self.assertRaises(ConflictError):
            self.open()
        self.apply.assert_not_called()

    def test_download_endpoint_authenticates_token_and_rechecks_task(self):
        import base64
        from types import SimpleNamespace

        from app.services.development import profiles
        result = self.open()
        workspace = self.row(result)
        secret = SimpleNamespace(data={"S3_PROFILE_TOKEN": base64.b64encode(b"test-token").decode()})
        core = MagicMock()
        core.read_namespaced_secret.return_value = secret
        with patch.object(profiles, "api_client"), patch.object(profiles.client, "CoreV1Api", return_value=core):
            from app.exceptions import UnauthorizedError
            with self.assertRaises(UnauthorizedError):
                task_workspaces.input_file(self.session, workspace.namespace, workspace.statefulset_name, "wrong", 0)
            def download(session, artifact, destination):
                destination.write_bytes(b"model")
            with patch.object(handoff_sources, "download", side_effect=download):
                path = task_workspaces.input_file(self.session, workspace.namespace, workspace.statefulset_name,
                                                "test-token", 0)
                self.assertEqual(path.read_bytes(), b"model")
                path.unlink()
            teams.set_member(self.session, self.admin, self.team.id, 2, active=False)
            with self.assertRaises(ForbiddenError):
                task_workspaces.input_file(
                    self.session, workspace.namespace, workspace.statefulset_name, "test-token", 0,
                )

    def test_changed_configuration_conflicts(self):
        self.open()
        with self.assertRaises(ConflictError):
            self.open(request=OpenTaskWorkspace(workspace={"image": "other:image"}))
        self.apply.assert_called_once()

    def test_crash_after_intent_or_uncertain_apply_recovers_same_identity(self):
        self.apply.side_effect = RuntimeError("uncertain apply")
        with self.assertRaises(ServiceUnavailableError):
            self.open()
        first = self.session.exec(select(CodeServerEntity)).one()
        self.assertEqual(first.status, "blocked")
        self.apply.side_effect = None
        result = self.open()
        self.assertEqual(first.id, result.workspace.id)
        names = [call.kwargs["statefulset_name"] for call in self.apply.call_args_list]
        self.assertEqual(names[0], names[1])

    def test_team_member_submitter_and_admin_cannot_implicitly_open(self):
        for claims in (self.claims(3), self.submitter, self.admin, self.claims(5)):
            with self.subTest(claims=claims), self.assertRaises(ForbiddenError):
                self.open(claims)
        self.apply.assert_not_called()

    def test_admin_recovery_is_explicit_and_audited(self):
        result = self.open(self.admin, OpenTaskWorkspace(admin_recovery_reason="Recover abandoned task"))
        self.assertEqual(self.row(result).owner_user_id, 1)
        event = preparation.list_events(self.session, self.admin, self.task.id)[0]
        self.assertEqual(event.changes["admin_recovery_reason"], "Recover abandoned task")
        access.guard(self.session, self.row(result))
        with self.assertRaises(ForbiddenError):
            self.open(self.engineer, OpenTaskWorkspace(admin_recovery_reason="not an admin"))

    def test_revoked_membership_blocks_proxy_and_input_access(self):
        result = self.open()
        teams.set_member(self.session, self.admin, self.team.id, 2, active=False)
        with self.assertRaises(ForbiddenError):
            workspaces.get_owned_code_server(self.session, self.engineer, result.workspace.id)
        with self.assertRaises(ForbiddenError):
            self.open()
        self.assertEqual(workspaces.list_code_servers(self.session, self.engineer), [])

    def test_released_claim_blocks_existing_workspace(self):
        result = self.open()
        task = preparation.get_task(self.session, self.engineer, self.task.id)
        preparation.release_claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=task.revision))
        with self.assertRaises(ForbiddenError):
            access.guard(self.session, self.row(result))

    def test_cancelled_task_blocks_but_workspace_can_be_deleted(self):
        result = self.open()
        task = preparation.get_task(self.session, self.engineer, self.task.id)
        preparation.cancel(self.session, self.engineer, task.id, ReasonRequest(
            expected_revision=task.revision, reason="No longer needed",
        ))
        with self.assertRaises(ConflictError):
            access.guard(self.session, self.row(result))
        with patch.object(k8s, "delete_code_server_resources", return_value="deleted"):
            workspaces.delete_code_server(self.session, self.engineer, result.workspace.id)
        self.assertIsNone(repository.get(self.session, self.task.id).workspace_id)

    def test_recreated_workspace_does_not_inherit_deleted_workspace_volume(self):
        first = self.open()
        with patch.object(k8s, "delete_code_server_resources", return_value="deleted"):
            workspaces.delete_code_server(self.session, self.admin, first.workspace.id)
        second = self.open()
        self.assertNotEqual(first.workspace.statefulset_name, second.workspace.statefulset_name)

    def test_changed_or_removed_profile_does_not_redirect_captured_sources(self):
        result = self.open()
        profile = self.session.get(S3ProfileEntity, 1)
        profile.bucket = "other"
        self.session.add(profile)
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            access.guard(self.session, self.row(result))
        with self.assertRaises(ForbiddenError):
            self.open()

    def test_snapshot_capture_uses_same_registered_input_interface(self):
        inputs = access.load_inputs(self.session, self.task.id)
        snapshot_id = self.task.source_snapshot_id
        repository.register_snapshot_inputs(self.session, snapshot_id, inputs)
        self.session.commit()
        self.task = self.claim(self.create(source_snapshot_id=snapshot_id))
        result = self.open()
        self.assertEqual(result.task_id, self.task.id)
        with self.assertRaises(ConflictError):
            repository.register_snapshot_inputs(self.session, snapshot_id,
                                               inputs.model_copy(update={"acceptance_criteria": "changed"}))

    def test_task_document_excludes_storage_credentials_and_selectors(self):
        self.open()
        document = self.apply.call_args.kwargs["task_document"]
        serialized = json.dumps(document)
        for text in ("encrypted", "access_key", "profile_id", "s3.test.invalid", "allowed/"):
            self.assertNotIn(text, serialized)
        self.assertEqual(document["acceptance_criteria"], "Accuracy >= 0.95")
        self.assertEqual(len(document["files"]), 3)

    def test_http_boundary_does_not_allow_inline_registry_credentials(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.engineer
        with TestClient(app) as client:
            path = f"/api/preparation/tasks/{self.task.id}/workspace"
            response = client.post(path, json={"workspace": {"dockerconfigjson": "secret"}})
            self.assertEqual(response.status_code, 422)
            response = client.post(path, json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["task_id"], str(self.task.id))

    def test_download_hash_version_and_size_before_publication(self):
        artifact = access.load_inputs(self.session, self.task.id).files[0].artifact
        client = MagicMock()
        for content, version, valid in [(b"model", "version-1", True), (b"other", "version-1", False),
                                        (b"model", "wrong", False), (b"too-long", "version-1", False)]:
            client.get_object.return_value = {"Body": io.BytesIO(content), "VersionId": version}
            with (tempfile.TemporaryDirectory() as directory,
                  patch.object(handoff_sources, "build_s3_client", return_value=client)):
                target = Path(directory) / "input"
                if valid:
                    handoff_sources.download(self.session, artifact, target)
                    self.assertEqual(target.read_bytes(), b"model")
                else:
                    with self.assertRaises(BadRequestError):
                        handoff_sources.download(self.session, artifact, target)
        self.assertEqual(client.get_object.call_args.kwargs["VersionId"], "version-1")
        self.assertFalse(client.put_object.called)


class TaskStagerTests(unittest.TestCase):
    def document(self):
        return {"files": [{"path": "source/model.bin", "size": 5,
                            "digest": "sha256:" + hashlib.sha256(b"model").hexdigest()}]}

    def test_partial_failure_never_publishes_inputs_and_retry_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                stage(root, self.document(), lambda _: io.BytesIO(b"wrong"))
            self.assertFalse((root / "verified").exists())
            self.assertFalse((root / "pending").exists())
            stage(root, self.document(), lambda _: io.BytesIO(b"model"))
            self.assertEqual((root / "verified/source/model.bin").read_bytes(), b"model")
            self.assertTrue((root / "verified/task.json").exists())

    def test_stager_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in ("/absolute", "../../escape", "source/../escape", "source\\escape"):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    stage(Path(directory), {"files": [{"path": path}]}, lambda _: io.BytesIO(b"model"))

    def test_readonly_volume_separate_from_writable_conversion_and_no_credentials(self):
        request = OpenTaskWorkspace().workspace
        manifests = k8s._manifests(request, "test", "task-1", "task-1-svc", "task-1-secret")
        configure(manifests, self.document(), "test", "task-1", "task-1-secret")
        spec = next(item for item in manifests if item["kind"] == "StatefulSet")["spec"]["template"]["spec"]
        main, init = spec["containers"][0], spec["initContainers"][0]
        self.assertIn({"name": "task-inputs", "mountPath": "/inputs", "readOnly": True}, main["volumeMounts"])
        self.assertIn("/workspace/conversion", main["args"][0])
        self.assertEqual(main["securityContext"]["capabilities"]["drop"], ["ALL"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(init["command"], ["python3", "/stager/stage.py"])
        self.assertNotIn("S3_SECRET", json.dumps(spec))
