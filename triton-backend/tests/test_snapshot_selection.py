"""Selected model budgets and safe, bounded S3 enumeration."""

import tempfile
import unittest
from pathlib import Path

from app.exceptions import BadRequestError
from app.schemas.serving_metadata import StartupModel
from app.services.candidates.artifacts import CaptureLimits
from app.services.snapshots.artifacts import SelectedModels
from tests.test_candidate_s3 import MemoryS3


class SnapshotSelectionTests(unittest.TestCase):
    def setUp(self):
        self.client = MemoryS3()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.source = SelectedModels(self.client, "source", "models", (StartupModel(name="iris", versions=("1",)),))

    def put(self, path, data=b"x"):
        self.client.put_object(Bucket="source", Key="models/" + path, Body=data)

    def test_unselected_models_versions_and_credentials_do_not_consume_byte_budget(self):
        # Arrange
        self.put("iris/1/model.onnx")
        for path in ("other/1/model.onnx", "iris/2/model.onnx", "iris/.env"):
            self.put(path, b"x" * 100)

        # Act
        captured = self.source.capture(self.root, CaptureLimits(max_bytes=1, max_file_bytes=1))

        # Assert
        self.assertEqual([entry.path for entry in captured.inventory.files], ["iris/1/model.onnx"])
        self.assertEqual(self.client.calls, [("models/iris/1/model.onnx", {"VersionId": "1"})])
        self.assertEqual((self.root / "iris/1/model.onnx").read_bytes(), b"x")

    def test_unselected_files_do_not_consume_retained_file_count(self):
        # Arrange
        self.put("iris/1/model.onnx")
        self.put("other/1/model.onnx")
        self.put("iris/2/model.onnx")

        # Act
        captured = self.source.capture(self.root, CaptureLimits(max_files=1))

        # Assert
        self.assertEqual(len(captured.inventory.files), 1)

    def test_selected_bytes_still_enforce_capture_budget(self):
        # Arrange
        self.put("iris/1/model.onnx", b"xx")

        # Act
        with self.assertRaises(BadRequestError) as error:
            self.source.capture(self.root, CaptureLimits(max_bytes=1))

        # Assert
        self.assertIn("byte limits", str(error.exception))
        self.assertEqual(self.client.calls, [])

    def test_selected_files_still_enforce_file_count(self):
        # Arrange
        self.put("iris/1/model.onnx")
        self.put("iris/config.pbtxt")

        # Act
        with self.assertRaises(BadRequestError) as error:
            self.source.capture(self.root, CaptureLimits(max_files=1))

        # Assert
        self.assertIn("file-count", str(error.exception))
        self.assertEqual(self.client.calls, [])

    def test_unselected_listing_is_still_bounded(self):
        # Arrange
        for index in range(4):
            self.put(f"other/1/file-{index}")

        # Act
        with self.assertRaises(BadRequestError) as error:
            self.source.capture(self.root, CaptureLimits(max_files=1))

        # Assert
        self.assertIn("Too many artifact paths", str(error.exception))
        self.assertEqual(self.client.calls, [])

    def test_selected_path_cannot_escape_staging(self):
        # Arrange
        self.put("iris/../escape")

        # Act
        with self.assertRaises(BadRequestError):
            self.source.capture(self.root, CaptureLimits())

        # Assert
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(self.client.calls, [])
