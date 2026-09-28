"""MLflow flavor for a complete Triton model repository directory."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from mlflow.artifacts import download_artifacts
from mlflow.exceptions import MlflowException
from mlflow.models import Model

FLAVOR_NAME = "triton"
_MODEL_DATA = "model"


def _validate_repository(model_path: Path) -> None:
    if not model_path.is_dir():
        raise MlflowException(f"Triton model directory does not exist: {model_path}")
    if model_path.is_symlink() or any(path.is_symlink() for path in model_path.rglob("*")):
        raise MlflowException("Triton model directory must not contain symbolic links")
    versions = [
        path for path in model_path.iterdir()
        if path.is_dir() and path.name.isascii() and path.name.isdecimal()
        and int(path.name) > 0
    ]
    if not versions:
        raise MlflowException(
            "Triton model directory must contain a numbered version"
        )


def save_model(triton_model_path, path, mlflow_model=None):
    """Save one Triton model directory as an MLflow Model with the triton flavor.

    ``triton_model_path`` points at the model-name directory containing
    ``config.pbtxt`` (if needed) and numbered version directories.
    """
    source = Path(triton_model_path).expanduser()
    _validate_repository(source)
    source = source.resolve()
    destination = Path(path).expanduser().resolve()
    if destination.exists():
        raise MlflowException(f"MLflow model path already exists: {destination}")
    if destination == source or destination.is_relative_to(source):
        raise MlflowException("MLflow model path must be outside the Triton model directory")

    destination.mkdir(parents=True)
    shutil.copytree(source, destination / _MODEL_DATA)
    model = mlflow_model or Model()
    model.add_flavor(FLAVOR_NAME, data=_MODEL_DATA, model_name=source.name)
    model.save(str(destination / "MLmodel"))


def log_model(
    triton_model_path,
    name="triton-model",
    registered_model_name=None,
    await_registration_for=300,
    run_id=None,
    metadata=None,
):
    """Log a Triton repository and optionally register a model version.

    Returns MLflow's ``ModelInfo``. A registry version is created only when
    ``registered_model_name`` is given.
    """
    return Model.log(
        artifact_path=None,
        flavor=sys.modules[__name__],
        name=name,
        registered_model_name=registered_model_name,
        await_registration_for=await_registration_for,
        run_id=run_id,
        metadata=metadata,
        flavor_name=FLAVOR_NAME,
        triton_model_path=triton_model_path,
    )


def load_model(model_uri):
    """Download an MLflow Triton model and return its repository directory."""
    model_root = Path(download_artifacts(artifact_uri=model_uri)).resolve()
    model = Model.load(str(model_root / "MLmodel"))
    flavor = model.flavors.get(FLAVOR_NAME)
    if not flavor:
        raise MlflowException(f"Model has no {FLAVOR_NAME} flavor: {model_uri}")
    data_path = (model_root / flavor.get("data", "")).resolve()
    if not data_path.is_relative_to(model_root) or data_path == model_root:
        raise MlflowException("Triton flavor contains an invalid data path")
    _validate_repository(data_path)
    return data_path
