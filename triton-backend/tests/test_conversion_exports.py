"""Conversion receipts and fail-closed retirement checks."""

import hashlib
import io
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.exceptions import ClientError
from botocore.response import StreamingBody

from app.exceptions import BadRequestError, ConflictError
from app.services.builds.contexts_storage import ContextStore
from app.services.conversion_exports import ConversionExport, ExportObject, verify_objects
from app.services.development import claim_inventory, claim_retention


class ExportVerificationTests(unittest.TestCase):
    def setUp(self):
        self.export = ConversionExport(
            export_id=uuid4(),
            profile_id=1,
            directory="models/iris",
            objects=[
                ExportObject(
                    key="exports/iris/" + path,
                    size=5,
                    sha256=hashlib.sha256(b"model").hexdigest(),
                    version_id=version,
                )
                for path, version in (("config.pbtxt", "v1"), ("1/model.onnx", "v2"))
            ],
        )
        self.client = MagicMock()
        self.client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        self.client.get_object.side_effect = lambda **kwargs: {
            "Body": StreamingBody(io.BytesIO(b"model"), 5),
            "ContentLength": 5,
            "VersionId": kwargs["VersionId"],
        }
        self.store = ContextStore(self.client, "models")

    def test_reads_exact_versions(self):
        # Arrange
        deadline = time.monotonic() + 60

        # Act
        verify_objects(self.store, self.export, "exports", deadline)

        # Assert
        calls = self.client.get_object.call_args_list
        self.assertEqual([call.kwargs["VersionId"] for call in calls], ["v1", "v2"])

    def test_rejects_corruption(self):
        # Arrange
        self.export.objects[1].sha256 = "0" * 64
        deadline = time.monotonic() + 60

        # Act
        with self.assertRaises(ConflictError) as caught:
            verify_objects(self.store, self.export, "exports", deadline)

        # Assert
        self.assertIn("checksum", str(caught.exception.detail))

    def test_rejects_inventory_missing_model_version(self):
        # Arrange
        self.export.objects.pop()
        deadline = time.monotonic() + 60

        # Act
        with self.assertRaises(BadRequestError) as caught:
            verify_objects(self.store, self.export, "exports", deadline)

        # Assert
        self.assertIn("version directories", str(caught.exception.detail))
        self.client.get_object.assert_not_called()

    def test_rejects_file_directory_collision_before_accessing_storage(self):
        # Arrange
        config = self.export.objects[0]
        self.export.objects.append(config.model_copy(update={"key": config.key + "/extra"}))
        deadline = time.monotonic() + 60

        # Act
        with self.assertRaises(BadRequestError) as caught:
            verify_objects(self.store, self.export, "exports", deadline)

        # Assert
        self.assertIn("both a file and directory", str(caught.exception.detail))
        self.client.get_bucket_versioning.assert_not_called()

    def test_reports_missing_object_version_as_conflict(self):
        # Arrange
        self.client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchVersion"}}, "GetObject")
        deadline = time.monotonic() + 60

        # Act
        with self.assertRaises(ConflictError) as caught:
            verify_objects(self.store, self.export, "exports", deadline)

        # Assert
        self.assertIn("object version is missing", str(caught.exception.detail))


class ClaimRetentionTests(unittest.TestCase):
    def setUp(self):
        self.claim = SimpleNamespace(
            metadata=SimpleNamespace(
                name="workspace-task-test-0",
                uid="claim-uid",
                annotations={
                    "triton-control.io/installation-id": "install",
                    "triton-control.io/workspace-name": "task-test",
                    "triton-control.io/task-id": str(uuid4()),
                },
            ),
            spec=SimpleNamespace(volume_name="pv"),
        )
        self.session, self.core, self.apps = MagicMock(), MagicMock(), MagicMock()
        self.enterContext(patch.object(claim_retention.developments, "find_by_workload", return_value=None))

    def evaluate(self):
        return claim_retention.evaluate(
            self.session, self.core, self.apps, "ns", self.claim, "install", timedelta(), True,
        )

    def test_retains_claims_with_unusable_identity(self):
        for identity in (None, "invalid-id"):
            with self.subTest(identity=identity):
                # Arrange
                annotations = self.claim.metadata.annotations
                annotations.pop("triton-control.io/task-id", None)
                if identity is not None:
                    annotations["triton-control.io/task-id"] = identity

                # Act
                result = self.evaluate()

                # Assert
                self.assertIn("legacy identity", result)
                self.core.delete_namespaced_persistent_volume_claim.assert_not_called()

    def test_retains_attached_claim(self):
        # Arrange
        self.apps.read_namespaced_stateful_set.return_value = SimpleNamespace()

        # Act
        result = self.evaluate()

        # Assert
        self.assertIn("active", result)
        self.core.delete_namespaced_persistent_volume_claim.assert_not_called()

    def test_retains_claim_after_export_is_interrupted(self):
        # Arrange
        def event(action, revision):
            return SimpleNamespace(
                action=action,
                revision=revision,
                changes={"workspace": "task-test", "namespace": "ns", "claim_uid": "claim-uid", "directory": "iris"},
            )

        self.session.exec.return_value.all.return_value = [
            event("conversion_export_verified", 1),
            event("conversion_export_interrupted", 2),
            event("workspace_deleted", 3),
        ]
        self.enterContext(patch.object(claim_retention, "attached", return_value=False))

        # Act
        result = self.evaluate()

        # Assert
        self.assertIn("incomplete", result)
        self.core.delete_namespaced_persistent_volume_claim.assert_not_called()

    def test_inventory_includes_files_outside_conversion_directory(self):
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "notes").write_bytes(b"unsaved")

        # Act
        inventory = claim_inventory.inventory(root)

        # Assert
        self.assertIn("notes", inventory)

    def test_inventory_rejects_symlinks(self):
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (root / "notes").write_bytes(b"unsaved")
        (root / "alias").symlink_to(root / "notes")

        # Act
        with self.assertRaises(ValueError) as caught:
            claim_inventory.inventory(root)

        # Assert
        self.assertIn("link or special file", str(caught.exception))

    def test_recoverability_requires_one_complete_export_per_model(self):
        root = "conversion/iris"
        config, weights = root + "/config.pbtxt", root + "/1/model"
        first = {config: "A", weights: "A"}
        second = {config: "B", weights: "B"}
        other = {"conversion/other/1/model": "C"}
        cases = [
            ("mixed", {config: "A", weights: "B"}, False),
            ("partial", {config: "A"}, False),
            ("extra", {**first, root + "/extra": "C"}, False),
            ("complete", {**first, **other}, True),
        ]
        for name, files, expected in cases:
            with self.subTest(name=name):
                # Arrange
                receipts = [(root, first), (root, second), ("conversion/other", other)]

                # Act
                result = claim_retention.recoverable(files, receipts)

                # Assert
                self.assertEqual(result, expected)
