"""Admin precedence and streaming large-model boundaries (Arrange/Act/Assert)."""

import os
import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.exceptions import BadRequestError, ForbiddenError, ServiceUnavailableError
from app.schemas.handoffs import S3Input
from app.schemas.preparation_settings import PreparationLimits
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import handoff_sources
from app.services import preparation_settings as settings
from app.services.development import kubernetes as k8s
from app.services.development.task_staging import configure
from tests.test_handoffs import HandoffFixture


class PreparationSettingsTests(HandoffFixture, unittest.TestCase):
    def test_defaults_match_requested_model_and_total_limits(self):
        # Arrange
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        # Act
        result = settings.read(self.session, self.admin)
        # Assert
        self.assertEqual(result.limits.model_dump(), {
            "max_object_gib": 10, "max_repository_gib": 15, "max_workspace_gib": 15,
            "capture_timeout_seconds": 3600, "download_timeout_seconds": 3600,
        })
        self.assertEqual(result.source, "deployment")

    def test_admin_override_and_reset_restore_helm_defaults(self):
        # Arrange
        self.enterContext(patch.dict(os.environ, {"PREPARATION_MAX_OBJECT_GIB": "12"}))
        requested = PreparationLimits(max_object_gib=24, max_repository_gib=32, max_workspace_gib=40)
        # Act
        saved = settings.update(self.session, self.admin, requested)
        loaded = settings.effective(self.session)
        reset = settings.update(self.session, self.admin, None)
        # Assert
        self.assertEqual(saved.source, "admin")
        self.assertEqual(loaded.max_object_gib, 24)
        self.assertEqual(reset.source, "deployment")
        self.assertEqual(reset.limits.max_object_gib, 12)

    def test_member_cannot_read_or_change_settings(self):
        # Arrange
        request = PreparationLimits()
        # Act / Assert
        for action in (lambda: settings.read(self.session, self.engineer),
                       lambda: settings.update(self.session, self.engineer, request),
                       lambda: settings.update(self.session, self.engineer, None)):
            with self.assertRaises(ForbiddenError):
                action()

    def test_inconsistent_limits_and_invalid_environment_fail_closed(self):
        # Arrange
        self.enterContext(patch.dict(os.environ, {"PREPARATION_MAX_OBJECT_GIB": "nonsense"}))
        # Act / Assert
        with self.assertRaises(ValidationError):
            PreparationLimits(max_object_gib=20, max_repository_gib=10)
        with self.assertRaises(ServiceUnavailableError):
            settings.effective(self.session)

    def test_twenty_gib_is_rejected_at_default_before_downloading(self):
        # Arrange
        client = MagicMock()
        client.head_object.return_value = {"VersionId": "v1", "ContentLength": 20 * 1024**3}
        self.enterContext(patch.object(handoff_sources, "build_s3_client", return_value=client))
        # Act
        with self.assertRaises(BadRequestError) as error:
            handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx"))
        # Assert
        self.assertIn("10 GiB", str(error.exception))
        client.get_object.assert_not_called()

    def test_admin_can_allow_twenty_gib_with_bounded_stream_reads(self):
        # Arrange: simulate 20 GiB without allocating/hash-processing 20 GiB in a unit test.
        settings.update(self.session, self.admin, PreparationLimits(
            max_object_gib=24, max_repository_gib=32, max_workspace_gib=40,
        ))
        chunk = b"x" * 1024**2
        remaining = 20 * 1024
        def read(size):
            nonlocal remaining
            if size != len(chunk):
                raise AssertionError("Reads must stay bounded to 1 MiB")
            if not remaining:
                return b""
            remaining -= 1
            return chunk
        body, client, checksum = MagicMock(), MagicMock(), MagicMock()
        body.read.side_effect = read
        checksum.hexdigest.return_value = "a" * 64
        client.head_object.return_value = {"VersionId": "v1", "ContentLength": 20 * 1024**3}
        client.get_object.return_value = {"VersionId": "v1", "Body": body}
        self.enterContext(patch.object(handoff_sources, "build_s3_client", return_value=client))
        self.enterContext(patch.object(handoff_sources.hashlib, "sha256", return_value=checksum))
        # Act
        result = handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx"))
        # Assert
        self.assertEqual(result.file.size, 20 * 1024**3)
        self.assertEqual(checksum.update.call_count, 20 * 1024)
        body.close.assert_called_once()

    def test_large_input_manifest_requests_enough_staging_disk(self):
        # Arrange: no data allocation; verify Kubernetes admission for a 20 GiB input.
        size = 20 * 1024**3
        document = {"files": [{"path": "source/model.bin", "size": size}], "download_timeout_seconds": 7200}
        manifests = k8s._manifests(OpenTaskWorkspace().workspace, "test", "task-1", "task-1-svc", "task-1-secret")
        # Act
        configure(manifests, document, "test", "task-1", "task-1-secret")
        # Assert
        spec = next(item for item in manifests if item["kind"] == "StatefulSet")["spec"]["template"]["spec"]
        volume = next(item for item in spec["volumes"] if item["name"] == "task-inputs")
        self.assertEqual(int(volume["emptyDir"]["sizeLimit"]), size + 1024**3)
        self.assertEqual(int(spec["initContainers"][0]["resources"]["requests"]["ephemeral-storage"]), size + 1024**3)
