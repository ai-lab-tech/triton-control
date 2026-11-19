"""Captured experiment inputs survive source changes and retain task authorization."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.db.entities import CodeServerEntity
from app.exceptions import ConflictError, ForbiddenError
from app.schemas.lineage import InventoryV1
from app.schemas.preparation import TaskRevision
from app.schemas.task_workspaces import OpenTaskWorkspace, SnapshotFile, WorkspaceInput, WorkspaceInputs
from app.services import preparation, task_workspace_access, task_workspaces
from app.services.development import kubernetes, profiles, workspaces
from app.services.development.task_stage_runner import stage
from app.services.lineage import inventory_digest
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

    def s3_download_fixture(self):
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
        return client, artifact, destination, data, key

    def test_s3_download_uses_retained_version(self):
        # Arrange
        client, artifact, destination, data, key = self.s3_download_fixture()

        # Act
        inputs.download(self.session, artifact, destination)

        # Assert
        self.assertEqual(destination.read_bytes(), data)
        client.get_object.assert_called_once_with(Bucket="retained", Key=key, VersionId="pinned")
        self.assertTrue(client.get_object.return_value["Body"].closed)

    def test_snapshot_download_uses_configured_transfer_timeout(self):
        from app.schemas.preparation_settings import PreparationLimits
        from app.services import preparation_settings
        client, artifact, destination, data, _ = self.s3_download_fixture()
        preparation_settings.update(self.session, self.admin, PreparationLimits(transfer_timeout_seconds=3600))
        with patch.object(inputs.time, "monotonic", side_effect=[0, 121]):
            inputs.download(self.session, artifact, destination)
        self.assertEqual(destination.read_bytes(), data)
        preparation_settings.update(self.session, self.admin, PreparationLimits(transfer_timeout_seconds=60))
        client.get_object.return_value["Body"] = io.BytesIO(data)
        with patch.object(inputs.time, "monotonic", side_effect=[0, 61]):
            with self.assertRaisesRegex(ConflictError, "deadline"):
                inputs.download(self.session, artifact, destination)

    def test_s3_download_rejects_wrong_version(self):
        # Arrange
        client, artifact, destination, data, _ = self.s3_download_fixture()
        client.get_object.return_value = {"VersionId": "replacement", "Body": io.BytesIO(data)}

        # Act
        with self.assertRaises(ConflictError) as error:
            inputs.download(self.session, artifact, destination)

        # Assert
        self.assertIn("version changed", str(error.exception))
        self.assertTrue(client.get_object.return_value["Body"].closed)

    def test_large_inventory_is_validated_once_per_authorization(self):
        # Arrange: a supported 1,000-file snapshot below the manifest byte limit.
        receipt = self.finish()
        original = receipt.manifest.inventory.files[0]
        inventory = InventoryV1(files=tuple(original.model_copy(update={"path": f"iris/1/file-{i}"})
                                            for i in range(1000)))
        snapshot = receipt.manifest.source_snapshot.model_copy(update={"inventory": inventory_digest(inventory)})
        manifest = receipt.manifest.model_copy(update={"inventory": inventory, "source_snapshot": snapshot})
        row = SimpleNamespace(manifest=manifest.model_dump(mode="json"),
                              artifacts=receipt.artifacts.model_dump(mode="json"))
        captured = WorkspaceInputs(snapshot=snapshot, files=[
            WorkspaceInput(role="source", artifact=SnapshotFile(snapshot_id=receipt.id, file=file))
            for file in inventory.files
        ])
        self.enterContext(patch.object(inputs.experiment_snapshots, "get", return_value=row))
        validate = self.enterContext(patch.object(inputs.SnapshotManifest, "model_validate",
                                                wraps=inputs.SnapshotManifest.model_validate))

        # Act
        task_workspace_access.authorize_inputs(self.session, captured)

        # Assert: parsing cost is per snapshot, not per file.
        self.assertEqual(validate.call_count, 1)

    def test_batch_authorization_rejects_tampered_file_identity(self):
        # Arrange
        receipt = self.finish()
        captured = task_workspace_access.load_inputs(self.session, receipt.task.id)
        artifacts = [item.artifact for item in captured.files]
        artifacts[-1] = artifacts[-1].model_copy(update={
            "file": artifacts[-1].file.model_copy(update={"digest": "sha256:" + "a" * 64}),
        })

        # Act
        with self.assertRaises(ForbiddenError) as error:
            inputs.authorize_files(self.session, artifacts, captured.snapshot)

        # Assert
        self.assertIn("not part", str(error.exception))

    def test_batch_authorization_rejects_foreign_snapshot_id(self):
        # Arrange
        receipt = self.finish()
        captured = task_workspace_access.load_inputs(self.session, receipt.task.id)
        artifacts = [item.artifact for item in captured.files]
        artifacts[-1] = artifacts[-1].model_copy(update={"snapshot_id": uuid4()})

        # Act
        with self.assertRaises(ForbiddenError) as error:
            inputs.authorize_files(self.session, artifacts, captured.snapshot)

        # Assert
        self.assertIn("not part", str(error.exception))

    def test_authorization_does_not_cache_storage_binding_across_requests(self):
        # Arrange
        receipt = self.finish()
        captured = task_workspace_access.load_inputs(self.session, receipt.task.id)
        task_workspace_access.authorize_inputs(self.session, captured)

        # Act
        with patch.dict(os.environ, {"TRITON_PREPARATION_SNAPSHOT_ROOT": str(self.root / "changed")}):
            with self.assertRaises(ForbiddenError) as error:
                task_workspace_access.authorize_inputs(self.session, captured)

        # Assert
        self.assertIn("binding changed", str(error.exception))
