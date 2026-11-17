"""Captured experiment inputs survive source changes and retain task authorization."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.db.entities import CodeServerEntity
from app.exceptions import ConflictError, ForbiddenError
from app.schemas.preparation import TaskRevision
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import preparation, task_workspace_access, task_workspaces
from app.services.development import kubernetes, profiles, workspaces
from app.services.development.task_stage_runner import stage
from app.services.snapshots import inputs
from tests.test_experiment_snapshots import SnapshotFixture


class SnapshotWorkspaceTests(SnapshotFixture, unittest.TestCase):
    def test_claimed_snapshot_opens_and_stages_retained_bytes_without_clearing_gaps(self):
        # Arrange
        receipt = self.finish()
        task = preparation.claim(self.session, self.engineer, receipt.task.id,
                                 TaskRevision(expected_revision=receipt.task.revision))
        self.source.data = b"changed after capture"
        self.enterContext(patch.object(workspaces, "require_kubernetes_enabled"))
        self.enterContext(patch.object(task_workspaces, "apply_registry_settings", side_effect=lambda req, _: req))
        apply = self.enterContext(patch.object(kubernetes, "apply_code_server_resources", return_value=["fixture"]))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # Act
        opened = task_workspaces.open_workspace(self.session, self.engineer, task.id, OpenTaskWorkspace())
        workspace = self.session.get(CodeServerEntity, opened.workspace.id)
        self.enterContext(patch.object(profiles, "authenticate_workspace", return_value=workspace))
        captured = task_workspace_access.load_inputs(self.session, task.id)
        def fetch(index):
            path = task_workspaces.input_file(self.session, workspace.namespace, workspace.name, "fixture", index)
            self.addCleanup(path.unlink, missing_ok=True)
            return path.open("rb")
        stage(root, task_workspaces.document(task.id, captured), fetch)
        # Assert
        self.assertEqual((root / "verified/source/iris/1/model.onnx").read_bytes(), b"synthetic model weights")
        self.assertFalse((root / "verified/source/iris/1/model.onnx").stat().st_mode & 0o222)
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).missing_inputs, task.missing_inputs)
        self.assertTrue(task.missing_inputs)
        apply.assert_called_once()

    def test_snapshot_workspace_requires_current_assignee(self):
        # Arrange
        receipt = self.finish()
        # Act
        with self.assertRaises(ForbiddenError):
            task_workspace_access.authorize(self.session, self.engineer, receipt.task.id)
        # Assert
        self.assertIsNone(preparation.get_task(self.session, self.engineer, receipt.task.id).assignee_user_id)

    def test_storage_rebinding_revokes_snapshot_inputs(self):
        # Arrange
        receipt = self.finish()
        captured = task_workspace_access.load_inputs(self.session, receipt.task.id)
        # Act
        with patch.dict(os.environ, {"TRITON_PREPARATION_SNAPSHOT_ROOT": str(self.root / "other")}):
            with self.assertRaises(ForbiddenError) as error:
                task_workspace_access.authorize_inputs(self.session, captured)
        # Assert
        self.assertIn("binding changed", str(error.exception))

    def test_tampered_retained_file_cannot_be_downloaded(self):
        # Arrange
        receipt = self.finish()
        captured = task_workspace_access.load_inputs(self.session, receipt.task.id)
        artifact = captured.files[0].artifact
        path = self.root / receipt.artifacts.reference["snapshot_id"] / "repository" / artifact.file.path
        path.chmod(0o644)
        path.write_bytes(b"x" * artifact.file.size)
        destination = Path(self.enterContext(tempfile.TemporaryDirectory())) / "input"
        # Act
        with self.assertRaises(ConflictError) as error:
            inputs.download(self.session, artifact, destination)
        # Assert
        self.assertIn("inventory", str(error.exception))

    def test_s3_download_uses_retained_version_and_rejects_wrong_version(self):
        # Arrange
        receipt = self.finish()
        artifact = task_workspace_access.load_inputs(self.session, receipt.task.id).files[0].artifact
        data = (self.root / receipt.artifacts.reference["snapshot_id"] / "repository" / artifact.file.path).read_bytes()
        key = "staging/capture/repository/" + artifact.file.path
        row = SimpleNamespace(manifest=receipt.manifest.model_dump(mode="json"), artifacts={"kind": "s3", "reference": {
            "bucket": "retained", "prefix": "staging/capture",
            "storage_binding": {"bucket": "retained", "prefix": "staging"},
            "objects": [{"area": "repository", "path": artifact.file.path, "key": key, "version_id": "pinned"}],
        }})
        client = MagicMock()
        client.get_object.return_value = {"VersionId": "pinned", "Body": io.BytesIO(data)}
        self.enterContext(patch.object(inputs, "authorize", return_value=row))
        self.enterContext(patch.object(inputs, "binding", return_value=(
            {"bucket": "retained", "prefix": "staging"}, {},
        )))
        self.enterContext(patch.object(inputs, "build_s3_client", return_value=client))
        destination = Path(self.enterContext(tempfile.TemporaryDirectory())) / "input"
        # Act
        inputs.download(self.session, artifact, destination)
        # Assert
        self.assertEqual(destination.read_bytes(), data)
        client.get_object.assert_called_once_with(Bucket="retained", Key=key, VersionId="pinned")
        self.assertTrue(client.get_object.return_value["Body"].closed)
        # Arrange
        client.get_object.return_value = {"VersionId": "replacement", "Body": io.BytesIO(data)}
        # Act
        with self.assertRaises(ConflictError) as error:
            inputs.download(self.session, artifact, destination)
        # Assert
        self.assertIn("version changed", str(error.exception))
        self.assertTrue(client.get_object.return_value["Body"].closed)
