"""Task authorization, durable retries, pinned inputs and workspace isolation."""

import base64
import hashlib
import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.task_workspaces_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import CodeServerEntity, S3ProfileEntity, TaskWorkspaceEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError, ServiceUnavailableError, UnauthorizedError
from app.repositories import task_workspaces as repository
from app.schemas.development import CreateCodeServerRequest
from app.schemas.preparation import ReasonRequest, TaskRevision
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import handoff_sources, preparation, task_workspaces, teams
from app.services import task_workspace_access as access
from app.services.development import kubernetes as k8s
from app.services.development import profiles, workspaces
from app.services.development.task_stage_runner import stage
from app.services.development.task_staging import configure
from tests.test_handoffs import HandoffFixture


class TaskWorkspaceTests(HandoffFixture, unittest.TestCase):
    def seed(self):
        super().seed()
        self.enterContext(patch.object(workspaces, "require_kubernetes_enabled"))
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

    def test_conversion_history_survives_replacement_and_retry(self):
        from uuid import uuid4
        from app.services import conversion_exports, engineering_queue
        first = self.row(self.open())
        identity = uuid4()
        prefix = f"allowed/task-conversions/{self.task.id}/{identity}/iris/"
        request = conversion_exports.ConversionExport.model_validate({
            "directory": "iris", "profile_id": 1, "export_id": identity, "objects": [
                {"key": prefix + path, "size": 5, "sha256": hashlib.sha256(b"model").hexdigest(), "version_id": "v1"}
                for path in ["config.pbtxt", "1/model.onnx"]],
        })
        with patch.object(conversion_exports, "authenticate_workspace", return_value=first), \
             patch.object(conversion_exports, "api_client"), patch.object(conversion_exports.client, "CoreV1Api") as core, \
             patch.object(conversion_exports, "storage") as backend:
            core.return_value.read_namespaced_persistent_volume_claim.return_value.metadata.uid = "old-pvc"
            provider = MagicMock()
            provider.get_bucket_versioning.return_value = {"Status": "Enabled"}
            provider.get_object.side_effect = lambda **args: {
                "VersionId": args["VersionId"], "ContentLength": 5,
                "Body": SimpleNamespace(iter_chunks=lambda **_: [b"model"], close=lambda: None),
            }
            backend.return_value.__enter__.return_value = conversion_exports.ContextStore(provider, "models")
            for status in ["started", "interrupted"]:
                conversion_exports.export_event(self.session, first.namespace, first.name, "token", request,
                                                "conversion_export_" + status)
            self.assertEqual(conversion_exports.saved_conversions(self.session, first.namespace, first.name, "token"), [])
            for _ in range(2):
                conversion_exports.record(self.session, first.namespace, first.name, "token", request)
            overview = engineering_queue.overview(self.session, self.engineer, self.task.id)
            self.assertEqual([(c.status, c.directory) for c in overview.conversions], [("verified", "iris")])
        with patch.object(k8s, "delete_code_server_resources", return_value="retired"):
            workspaces.delete_code_server(self.session, self.engineer, first.id)
        second = self.row(self.open())
        with patch.object(conversion_exports, "authenticate_workspace", return_value=second):
            receipt = conversion_exports.saved_receipt(self.session, second.namespace, second.name, "token", identity)
        self.assertEqual(receipt["claim_uid"], "old-pvc")
        self.assertEqual([o["version_id"] for o in receipt["objects"]], ["v1", "v1"])

    def test_size_preview_does_not_allocate_and_matches_creation(self):
        # Arrange
        client = self.http_client()
        # Act
        response = client.get(f"/api/preparation/tasks/{self.task.id}/workspace-sizing")
        # Assert
        self.assertEqual(response.status_code, 200)
        sizing = response.json()
        self.assertEqual(sizing["recommended_gib"], 6)
        self.assertIsNone(repository.get(self.session, self.task.id))
        self.apply.assert_not_called()
        # Act
        self.open()
        # Assert
        self.assertEqual(self.apply.call_args.args[0].storage_size, "6Gi")

    def test_size_preview_requires_assignment(self):
        # Arrange
        # Act / Assert
        with self.assertRaises(ForbiddenError):
            task_workspaces.workspace_sizing(self.session, self.submitter, self.task.id)
        self.apply.assert_not_called()

    def test_storage_cap_rejects_before_reserving_workspace(self):
        # Arrange
        request = OpenTaskWorkspace(workspace={"storage_size": "51Gi"})
        # Act / Assert
        with self.assertRaises(BadRequestError):
            self.open(request=request)
        self.assertIsNone(repository.get(self.session, self.task.id))
        self.apply.assert_not_called()

    def test_reopen_preserves_allocation_after_settings_change(self):
        # Arrange
        first = self.open()
        from app.schemas.preparation_settings import PreparationLimits
        limits = PreparationLimits(max_workspace_storage_gib=10)
        self.enterContext(patch.object(task_workspaces, "effective", return_value=limits))
        # Act
        second = self.open(request=OpenTaskWorkspace(workspace={"storage_size": "6Gi"}))
        sizing = task_workspaces.workspace_sizing(self.session, self.engineer, self.task.id)
        # Assert
        self.assertEqual(first.workspace.id, second.workspace.id)
        self.assertEqual(sizing.existing_storage_size, "6Gi")
        self.assertEqual(repository.get(self.session, self.task.id).configuration["storage_size"], "6Gi")
        self.apply.assert_called_once()

    def test_reopen_uses_saved_image_and_resources_without_overrides(self):
        # Arrange
        options = {"image": "registry.example/converter:2", "gpu_count": 1,
                   "cpu": "1500m", "memory": "4096Mi", "storage_size": "8Gi"}
        first = self.open(request=OpenTaskWorkspace(workspace=options))

        # Act
        sizing = task_workspaces.workspace_sizing(self.session, self.engineer, self.task.id)
        reopened = self.open()

        # Assert
        self.assertEqual(reopened.workspace.id, first.workspace.id)
        self.assertEqual(sizing.existing_image, options["image"])
        self.assertEqual(sizing.existing_gpu_count, options["gpu_count"])
        self.assertEqual(sizing.existing_cpu, options["cpu"])
        self.assertEqual(sizing.existing_memory, options["memory"])
        self.assertEqual(sizing.existing_storage_size, options["storage_size"])
        self.apply.assert_called_once()

    def test_missing_inputs_block_start(self):
        incomplete = self.submit(self.request(revision="incomplete"))
        self.task = self.claim(incomplete.task)
        with self.assertRaises(ConflictError):
            self.open()
        self.apply.assert_not_called()

    def authenticated_workspace(self):
        workspace = self.row(self.open())
        secret = SimpleNamespace(data={"S3_PROFILE_TOKEN": base64.b64encode(b"test-token").decode()})
        core = MagicMock()
        core.read_namespaced_secret.return_value = secret
        self.enterContext(patch.object(profiles, "api_client"))
        self.enterContext(patch.object(profiles.client, "CoreV1Api", return_value=core))
        return workspace

    def test_download_rejects_invalid_token(self):
        workspace = self.authenticated_workspace()

        with self.assertRaises(UnauthorizedError):
            task_workspaces.input_file(self.session, workspace.namespace, workspace.statefulset_name, "wrong", 0)

    def test_download_returns_verified_bytes(self):
        workspace = self.authenticated_workspace()
        def download(session, artifact, destination):
            destination.write_bytes(b"model")
        self.enterContext(patch.object(handoff_sources, "download", side_effect=download))

        path = task_workspaces.input_file(
            self.session, workspace.namespace, workspace.statefulset_name, "test-token", 0,
        )
        self.addCleanup(path.unlink, missing_ok=True)

        self.assertEqual(path.read_bytes(), b"model")

    def test_download_rechecks_revoked_membership(self):
        workspace = self.authenticated_workspace()
        teams.set_member(self.session, self.admin, self.team.id, 2, active=False)
        download = self.enterContext(patch.object(handoff_sources, "download"))

        with self.assertRaises(ForbiddenError):
            task_workspaces.input_file(
                self.session, workspace.namespace, workspace.statefulset_name, "test-token", 0,
            )

        download.assert_not_called()

    def test_retry_after_status_refresh_completes_partial_provisioning(self):
        for status in ("creating", "ready"):
            with self.subTest(status=status):
                self.task = self.claim(self.submit(self.request(
                    revision="partial-" + status,
                    dataset={"profile_id": 1, "path": "dataset.json"},
                    baseline={"profile_id": 1, "path": "baseline.json"},
                    expected_behavior="Predict iris class", acceptance_criteria="Accuracy >= 0.95",
                )).task)
                self.apply.reset_mock()
                self.apply.side_effect = RuntimeError("Service failed after StatefulSet creation")
                with self.assertRaises(ServiceUnavailableError):
                    self.open()
                workspace_id = repository.get(self.session, self.task.id).workspace_id
                with (patch.object(workspaces, "kubernetes_enabled", return_value=True),
                      patch.object(k8s, "read_status", return_value=(status, "Pod status"))):
                    workspaces.get_code_server(self.session, self.engineer, workspace_id)
                self.apply.side_effect = None

                result = self.open()

                self.assertEqual(result.workspace.id, workspace_id)
                self.assertEqual(self.apply.call_count, 2)
                self.assertTrue(self.row(result).applied_resources)
                names = [call.kwargs["statefulset_name"] for call in self.apply.call_args_list]
                self.assertEqual(names[0], names[1])

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
        listed, = workspaces.list_code_servers(self.session, self.engineer)
        self.assertEqual((listed.id, listed.status, listed.url), (result.workspace.id, "cleanup_only", ""))
        self.assertIsNone(listed.task_title)

    def test_task_workspace_title_is_available_in_workspace_navigation(self):
        # Arrange
        opened = self.open()

        # Act
        available = workspaces.list_code_servers(self.session, self.engineer)

        # Assert
        self.assertEqual(available[0].storage_size, "6Gi")
        self.assertEqual(opened.workspace.task_title, self.task.title)
        self.assertEqual([(item.id, item.task_title) for item in available], [(opened.workspace.id, self.task.title)])

    def test_personal_workspace_can_be_created_after_task_workspace(self):
        # Arrange
        self.open()
        request = CreateCodeServerRequest()

        # Act
        personal = workspaces.create_code_server(request, self.session, self.engineer)

        # Assert
        self.assertIsNone(personal.task_title)
        with self.assertRaises(BadRequestError):
            workspaces.create_code_server(request, self.session, self.engineer)

    def test_released_claim_blocks_existing_workspace(self):
        result = self.open()
        task = preparation.get_task(self.session, self.engineer, self.task.id)
        preparation.release_claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=task.revision))
        listed, = workspaces.list_code_servers(self.session, self.engineer)
        self.assertEqual((listed.id, listed.status, listed.url), (result.workspace.id, "cleanup_only", ""))
        with self.assertRaises(ForbiddenError):
            access.guard(self.session, self.row(result))
        with patch.object(k8s, "delete_code_server_resources", return_value="deleted"):
            workspaces.delete_code_server(self.session, self.engineer, listed.id)
        self.assertEqual(workspaces.list_code_servers(self.session, self.engineer), [])

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

    def http_client(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.engineer
        return self.enterContext(TestClient(app))

    def test_http_boundary_does_not_allow_inline_registry_credentials(self):
        client = self.http_client()
        path = f"/api/preparation/tasks/{self.task.id}/workspace"

        response = client.post(path, json={"workspace": {"dockerconfigjson": "secret"}})

        self.assertEqual(response.status_code, 422)
        self.apply.assert_not_called()

    def test_http_boundary_opens_workspace_with_defaults(self):
        client = self.http_client()
        path = f"/api/preparation/tasks/{self.task.id}/workspace"

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

    def test_partial_failure_never_publishes_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                stage(root, self.document(), lambda _: io.BytesIO(b"wrong"))
            self.assertFalse((root / "verified").exists())
            self.assertFalse((root / "pending").exists())

    def test_retry_after_partial_failure_publishes_verified_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                stage(root, self.document(), lambda _: io.BytesIO(b"wrong"))

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

    def test_claim_retains_task_identity_after_statefulset_deletion(self):
        manifests = k8s._manifests(OpenTaskWorkspace().workspace, "test", "task-1", "svc", "secret")
        with patch.dict("os.environ", {"TRITON_INSTALLATION_ID": "dev.example"}):
            configure(manifests, {**self.document(), "task_id": "task-id"}, "test", "task-1", "secret")

        spec = next(item["spec"] for item in manifests if item["kind"] == "StatefulSet")
        self.assertEqual(spec["persistentVolumeClaimRetentionPolicy"], {"whenScaled": "Retain", "whenDeleted": "Retain"})
        self.assertEqual(spec["volumeClaimTemplates"][0]["metadata"]["annotations"], {
            "triton-control.io/workspace-name": "task-1", "triton-control.io/task-id": "task-id",
            "triton-control.io/installation-id": "dev.example",
        })

    def test_editor_default_output_root_is_writable_conversion_folder(self):
        manifests = k8s._manifests(OpenTaskWorkspace().workspace, "test", "task-1", "task-1-svc", "task-1-secret")

        configure(manifests, self.document(), "test", "task-1", "task-1-secret")

        spec = next(item for item in manifests if item["kind"] == "StatefulSet")["spec"]["template"]["spec"]
        command = shlex.split(spec["containers"][0]["args"][0])
        folders = json.loads(command[command.index("printf") + 2])["folders"]
        # HF Builder and scaffolding resolve default output relative to folders[0].
        self.assertEqual(folders[0]["path"], "/workspace/conversion")
        self.assertEqual(folders[1]["path"], "/inputs/verified")
        mounts = spec["containers"][0]["volumeMounts"]
        self.assertFalse(next(m for m in mounts if m["mountPath"] == "/workspace").get("readOnly", False))
        self.assertTrue(next(m for m in mounts if m["mountPath"] == "/inputs")["readOnly"])
