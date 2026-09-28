"""Checks for packaging and retrieving a complete Triton repository."""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class MlflowException(Exception):
    pass


class Model:
    log = Mock()

    def __init__(self):
        self.flavors = {}

    def add_flavor(self, name, **params):
        self.flavors[name] = params
        return self

    def save(self, path):
        Path(path).write_text(json.dumps({"flavors": self.flavors}))

    @classmethod
    def load(cls, path):
        result = cls()
        result.flavors = json.loads(Path(path).read_text())["flavors"]
        return result


mlflow = types.ModuleType("mlflow")
artifacts = types.ModuleType("mlflow.artifacts")
exceptions = types.ModuleType("mlflow.exceptions")
models = types.ModuleType("mlflow.models")
deployments = types.ModuleType("mlflow.deployments")
artifacts.download_artifacts = Mock()
exceptions.MlflowException = MlflowException
models.Model = Model
deployments.BaseDeploymentClient = type("BaseDeploymentClient", (), {})
with patch.dict(sys.modules, {
    "mlflow": mlflow, "mlflow.artifacts": artifacts,
    "mlflow.exceptions": exceptions, "mlflow.models": models,
    "mlflow.deployments": deployments,
}):
    triton = importlib.import_module("mlflow_triton_control.triton")


class TritonFlavorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "iris_classifier"
        (self.source / "1").mkdir(parents=True)
        (self.source / "1" / "model.onnx").write_bytes(b"onnx model")
        (self.source / "config.pbtxt").write_text('name: "iris_classifier"')

    def test_save_and_load_preserve_repository_and_flavor(self):
        destination = self.root / "saved"
        triton.save_model(str(self.source) + "/", destination)

        self.assertEqual((destination / "model/1/model.onnx").read_bytes(), b"onnx model")
        self.assertEqual((destination / "model/config.pbtxt").read_text(), 'name: "iris_classifier"')
        saved = Model.load(destination / "MLmodel")
        self.assertEqual(saved.flavors["triton"], {"data": "model", "model_name": "iris_classifier"})

        with patch.object(triton, "download_artifacts", return_value=str(destination)):
            self.assertEqual(triton.load_model("models:/iris-serving/1"), destination / "model")

    def test_log_model_forwards_registry_name_to_mlflow(self):
        info = object()
        with (
            patch.dict(sys.modules, {"mlflow_triton_control.triton": triton}),
            patch.object(Model, "log", return_value=info) as log,
        ):
            result = triton.log_model(self.source, registered_model_name="iris-serving")
        self.assertIs(result, info)
        self.assertEqual(log.call_args.kwargs["registered_model_name"], "iris-serving")
        self.assertEqual(log.call_args.kwargs["flavor_name"], "triton")
        self.assertIs(log.call_args.kwargs["flavor"], triton)
        self.assertEqual(log.call_args.kwargs["triton_model_path"], self.source)

    def test_rejects_missing_version_and_symbolic_links(self):
        (self.source / "1" / "model.onnx").unlink()
        (self.source / "1").rmdir()
        with self.assertRaisesRegex(MlflowException, "numbered version"):
            triton.save_model(self.source, self.root / "saved")
        (self.source / "1").mkdir()
        (self.source / "1" / "model.onnx").symlink_to(self.source / "config.pbtxt")
        with self.assertRaisesRegex(MlflowException, "symbolic links"):
            triton.save_model(self.source, self.root / "saved")

    def test_rejects_non_triton_mlflow_model(self):
        destination = self.root / "saved"
        destination.mkdir()
        Model().save(destination / "MLmodel")
        with patch.object(triton, "download_artifacts", return_value=str(destination)):
            with self.assertRaisesRegex(MlflowException, "no triton flavor"):
                triton.load_model("models:/sklearn/1")


if __name__ == "__main__":
    unittest.main()
