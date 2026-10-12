"""Checks for packaging and retrieving a complete Triton repository."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mlflow.exceptions import MlflowException
from mlflow.models import Model
from mlflow_triton_control import triton


class TritonFlavorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "iris_classifier"
        (self.source / "1").mkdir(parents=True)
        (self.source / "1" / "model.onnx").write_bytes(b"onnx model")
        (self.source / "config.pbtxt").write_text('name: "iris_classifier"')

    def test_save_preserves_repository_and_flavor(self):
        # Arrange
        destination = self.root / "saved"

        # Act
        triton.save_model(str(self.source) + "/", destination)

        # Assert
        self.assertEqual(
            (destination / "model/1/model.onnx").read_bytes(), b"onnx model"
        )
        self.assertEqual(
            (destination / "model/config.pbtxt").read_text(), 'name: "iris_classifier"'
        )
        saved = Model.load(destination / "MLmodel")
        self.assertEqual(
            saved.flavors["triton"], {"data": "model", "model_name": "iris_classifier"}
        )

    def test_load_returns_triton_repository_directory(self):
        # Arrange
        destination = self.root / "saved"
        triton.save_model(self.source, destination)
        with patch.object(triton, "download_artifacts", return_value=str(destination)):
            # Act
            result = triton.load_model("models:/iris-serving/1")

        # Assert
        self.assertEqual(result, destination / "model")

    def test_log_model_forwards_registry_name_to_mlflow(self):
        # Arrange
        info = object()
        with patch.object(Model, "log", return_value=info) as log:
            # Act
            result = triton.log_model(self.source, registered_model_name="iris-serving")
        # Assert
        self.assertIs(result, info)
        self.assertEqual(log.call_args.kwargs["registered_model_name"], "iris-serving")
        self.assertIs(log.call_args.kwargs["flavor"], triton)
        self.assertEqual(log.call_args.kwargs["triton_model_path"], self.source)

    def test_rejects_missing_version(self):
        # Arrange
        (self.source / "1" / "model.onnx").unlink()
        (self.source / "1").rmdir()
        # Act / Assert
        with self.assertRaisesRegex(MlflowException, "numbered version"):
            triton.save_model(self.source, self.root / "saved")

    def test_rejects_symbolic_links(self):
        # Arrange
        (self.source / "1" / "model.onnx").unlink()
        (self.source / "1" / "model.onnx").symlink_to(self.source / "config.pbtxt")
        # Act / Assert
        with self.assertRaisesRegex(MlflowException, "symbolic links"):
            triton.save_model(self.source, self.root / "saved")

    def test_rejects_non_triton_mlflow_model(self):
        # Arrange
        destination = self.root / "saved"
        destination.mkdir()
        Model().save(destination / "MLmodel")

        # Act / Assert
        with (
            patch.object(triton, "download_artifacts", return_value=str(destination)),
            self.assertRaisesRegex(MlflowException, "no triton flavor"),
        ):
            triton.load_model("models:/sklearn/1")


if __name__ == "__main__":
    unittest.main()
