"""Check the published plugin through the actual shared-Python workspace installer."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


def verify(expected_plugin: str, expected_mlflow: str) -> None:
    from importlib.metadata import requires, version
    from unittest.mock import patch

    import mlflow
    import numpy as np
    from jupyter_client import KernelManager
    from mlflow.deployments import get_deploy_client
    from mlflow_triton import sklearn as triton_sklearn
    from packaging.requirements import Requirement
    from packaging.version import Version
    from sklearn.datasets import load_iris
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    assert Version(version("mlflow-triton-control")) == Version(expected_plugin)
    assert Version(mlflow.__version__) == Version(expected_mlflow)
    assert Version(np.__version__) < Version("2")
    core = next(
        Requirement(item)
        for item in requires("mlflow-triton-control") or []
        if Requirement(item).name == "mlflow-triton"
    )
    assert Version(version("mlflow-triton")) in core.specifier
    # Discovery only: no deployment requests or real credentials in this smoke.
    with patch.dict(
        os.environ,
        {
            "TRITON_CONTROL_URL": "http://127.0.0.1:1",
            "TRITON_CONTROL_TOKEN": "compatibility-test",
        },
    ):
        assert type(get_deploy_client("triton-control")).__module__.startswith("mlflow_triton_control")

    with tempfile.TemporaryDirectory(prefix="mlflow-plugin-smoke-") as folder:
        root = Path(folder)
        mlflow.set_tracking_uri(f"sqlite:///{root / 'tracking.db'}")
        experiment = mlflow.create_experiment("compatibility", artifact_location=(root / "artifacts").as_uri())
        iris = load_iris(as_frame=True)
        features = iris.data.astype(np.float32)
        features.columns = ["sepal_length", "sepal_width", "petal_length", "petal_width"]
        model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=250)).fit(features, iris.target)
        with mlflow.start_run(experiment_id=experiment):
            info = triton_sklearn.log_model(model, name="classifier", input_example=features.iloc[:2])
        artifacts = Path(mlflow.artifacts.download_artifacts(info.model_uri))
        repository = artifacts / "model_repository" / "classifier"
        for name in ("config.pbtxt", "requirements.txt", "request.json", "validation.json", "1/model.py"):
            assert (repository / name).is_file(), f"Missing generated artifact: {name}"
        assert (repository / "1/artifact").is_dir()

    manager = KernelManager(kernel_name="triton-control")
    manager.start_kernel()
    client = manager.client()
    client.start_channels()
    try:
        client.wait_for_ready(timeout=60)
        reply = client.execute_interactive(
            "import mlflow, mlflow_triton, mlflow_triton_control, numpy; "
            "from importlib.metadata import version; "
            f"assert version('mlflow-triton-control') == {expected_plugin!r}; "
            f"assert mlflow.__version__ == {expected_mlflow!r}",
            timeout=60,
        )
        assert reply["content"]["status"] == "ok", reply["content"]
    finally:
        client.stop_channels()
        manager.shutdown_kernel(now=True)
    print("Workspace Python, MLflow logging, generated artifacts, deployment target and notebook checks passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-version", required=True)
    parser.add_argument("--mlflow-version", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify(args.plugin_version, args.mlflow_version)
        return

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.schemas.development import CreateCodeServerRequest
    from app.services.development.mlflow_setup import setup_command

    command = setup_command(
        CreateCodeServerRequest(
            mlflow_version=args.mlflow_version,
            mlflow_triton_control_version=args.plugin_version,
        )
    )
    # A failing image-supplied module catches accidental precedence over user-site.
    with tempfile.TemporaryDirectory(prefix="dali-shadow-") as folder:
        Path(folder, "typing_extensions.py").write_text("raise ImportError('Loaded image typing_extensions')\n")
        env = {**os.environ, "PYTHONPATH": folder}
        verify_command = shlex.join(
            [
                "python3",
                str(Path(__file__).resolve()),
                "--verify",
                "--plugin-version",
                args.plugin_version,
                "--mlflow-version",
                args.mlflow_version,
            ]
        )
        # Check both a fresh setup and restart with packages already present.
        subprocess.run(["sh", "-ec", command + command + verify_command], env=env, check=True, timeout=600)


if __name__ == "__main__":
    main()
