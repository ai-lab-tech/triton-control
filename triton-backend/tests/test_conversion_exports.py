"""Conversion receipts and fail-closed retirement checks."""

import hashlib
import io
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from botocore.response import StreamingBody

from app.exceptions import BadRequestError, ConflictError
from app.services.builds.contexts_storage import ContextStore
from app.services.conversion_exports import ConversionExport, ExportObject, verify_objects
from app.services.development import claim_inventory, claim_retention


class ExportChecks(unittest.TestCase):
    def setUp(self):
        self.export = ConversionExport(export_id=uuid4(), profile_id=1, directory="models/iris", objects=[
            ExportObject(key="exports/iris/" + path, size=5, sha256=hashlib.sha256(b"model").hexdigest(), version_id=version)
            for path, version in (("config.pbtxt", "v1"), ("1/model.onnx", "v2"))
        ])
        self.client = MagicMock()
        self.client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        self.client.get_object.side_effect = lambda **kw: {
            "Body": StreamingBody(io.BytesIO(b"model"), 5), "ContentLength": 5, "VersionId": kw["VersionId"],
        }
        self.store = ContextStore(self.client, "models")

    def verify(self):
        verify_objects(self.store, self.export, "exports", time.monotonic() + 60)

    def test_reads_exact_versions(self):
        self.verify()
        self.assertEqual([call.kwargs["VersionId"] for call in self.client.get_object.call_args_list], ["v1", "v2"])

    def test_rejects_corruption_and_partial_inventory(self):
        self.export.objects[1].sha256 = "0" * 64
        with self.assertRaises(ConflictError):
            self.verify()
        self.export.objects.pop()
        with self.assertRaises(BadRequestError):
            self.verify()

    def test_legacy_and_attached_claims_are_protected(self):
        metadata = SimpleNamespace(name="workspace-task-test-0", uid="uid", annotations={})
        claim = SimpleNamespace(metadata=metadata, spec=SimpleNamespace(volume_name="pv"))
        session, core, apps = MagicMock(), MagicMock(), MagicMock()
        self.assertIn("legacy", claim_retention.evaluate(session, core, apps, "ns", claim, "install", None, True))
        metadata.annotations = {"triton-control.io/installation-id": "install", "triton-control.io/workspace-name":
                                "task-test", "triton-control.io/task-id": str(uuid4())}
        with patch.object(claim_retention.developments, "find_by_workload", return_value=None):
            self.assertIn("active", claim_retention.evaluate(session, core, apps, "ns", claim, "install", None, True))
        core.delete_namespaced_persistent_volume_claim.assert_not_called()

    def test_interrupted_export_after_receipt_protects_claim(self):
        task = uuid4()
        ns, name, uid = "ns", "task-test", "claim-uid"
        claim = SimpleNamespace(metadata=SimpleNamespace(name="workspace-task-test-0", uid=uid, annotations={
            "triton-control.io/task-id": str(task), "triton-control.io/workspace-name": name,
            "triton-control.io/installation-id": "test"}), spec=SimpleNamespace(volume_name="pv"))
        session, core, apps = MagicMock(), MagicMock(), MagicMock()
        def event(action, revision):
            return SimpleNamespace(action=action, revision=revision, changes={
                "workspace": name, "namespace": ns, "claim_uid": uid, "directory": "iris"})
        session.exec.return_value.all.return_value = [
            event("conversion_export_verified", 1), event("conversion_export_interrupted", 2),
            event("workspace_deleted", 3)]
        with patch.object(claim_retention.developments, "find_by_workload", return_value=None), \
             patch.object(claim_retention, "attached", return_value=False):
            result = claim_retention.evaluate(session, core, apps, ns, claim, "test", None, True)
        self.assertIn("incomplete", result)
        core.delete_namespaced_persistent_volume_claim.assert_not_called()

    def test_inspection_covers_other_directories_and_rejects_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes").write_bytes(b"unsaved")
            self.assertIn("notes", claim_inventory.inventory(root))
            (root / "alias").symlink_to(root / "notes")
            with self.assertRaises(ValueError):
                claim_inventory.inventory(root)
