"""Check the published plugin through the actual shared-Python workspace installer."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def verify_deployment(model_uri: str, repository: Path, connection: dict[str, str]) -> None:
    """Exercise the published client's HTTP contract against the real backend routes."""
    from unittest.mock import patch

    import httpx
    from mlflow.deployments import get_deploy_client
    from mlflow.exceptions import MlflowException

    name = "mlflow-contract"
    config = {"s3_profile_name": "models", "image": "triton:contract", "wait_timeout_seconds": 10}
    with patch.dict(
        os.environ,
        {
            "TRITON_CONTROL_URL": connection["url"],
            "TRITON_CONTROL_TOKEN": connection["token"],
        },
    ):
        client = get_deploy_client("triton-control")
        deployed = client.create_deployment(name, model_uri=model_uri, config=config)
        assert deployed["name"] == name and deployed["status"] == "ready", deployed
        assert client.get_deployment(name)["model_name"] == "classifier"
        assert [item["name"] for item in client.list_deployments()] == [name]
        response = httpx.get(connection["url"] + "/contract/applied")
        response.raise_for_status()
        (applied,) = response.json()
        assert applied["model_uri"].startswith("s3://compatibility-models/")
        assert applied["model_name"] == "classifier"
        assert applied["requirements_txt"].strip() == (repository / "requirements.txt").read_text().strip()
        assert applied["image"] == config["image"] and applied["model_control_mode"] == "explicit"

        with patch.dict(os.environ, {"TRITON_CONTROL_TOKEN": connection["other_token"]}):
            assert client.list_deployments() == []
            try:
                client.get_deployment(name)
            except MlflowException as error:
                assert "404" in str(error), str(error)
            else:
                raise AssertionError("Another owner could read the deployment")

        try:
            client.create_deployment(
                "foreign-profile", model_uri=model_uri, config={**config, "s3_profile_name": "other-owner"}
            )
        except MlflowException as error:
            assert "404" in str(error), str(error)
        else:
            raise AssertionError("Another owner's S3 profile was accepted")
        assert client.delete_deployment(name)["status"] == "deleted"
        assert client.list_deployments() == []
    print("Backend deployment create/get/list/delete, requirements transfer and ownership checks passed")


def verify(expected_plugin: str, expected_mlflow: str, contract_file: Path | None = None) -> None:
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
    assert np.__version__ in Requirement(os.environ["MLFLOW_NUMPY_REQUIREMENT"]).specifier
    core = next(
        Requirement(item)
        for item in requires("mlflow-triton-control") or []
        if Requirement(item).name == "mlflow-triton"
    )
    assert Version(version("mlflow-triton")) in core.specifier
    # Check entry-point discovery before testing deployment with the API fixture.
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
        # Only the object store is a fixture. MLflow model resolution and plugin HTTP
        # requests run unmodified against S3 URIs and the backend API, respectively.
        from urllib.parse import urlsplit

        from mlflow.store.artifact.artifact_repository_registry import _artifact_repository_registry
        from mlflow.store.artifact.local_artifact_repo import LocalArtifactRepository

        class FixtureS3Repository(LocalArtifactRepository):
            def __init__(self, artifact_uri, **kwargs):
                parsed = urlsplit(artifact_uri)
                assert parsed.netloc == "compatibility-models"
                super().__init__(str(root / "artifacts" / parsed.path.lstrip("/")), **kwargs)

        _artifact_repository_registry.register("s3", FixtureS3Repository)
        experiment = mlflow.create_experiment("compatibility", artifact_location="s3://compatibility-models/iris")
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
        if contract_file:
            deadline = time.monotonic() + 30
            while not contract_file.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Backend contract server did not start")
                time.sleep(0.1)
            verify_deployment(info.model_uri, repository, json.loads(contract_file.read_text()))

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
    parser.add_argument("--contract-file", type=Path)
    args = parser.parse_args()
    if args.verify:
        verify(args.plugin_version, args.mlflow_version, args.contract_file)
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
            + (["--contract-file", str(args.contract_file)] if args.contract_file else [])
        )
        # Check both a fresh setup and restart with packages already present.
        subprocess.run(["sh", "-ec", command + command + verify_command], env=env, check=True, timeout=600)


if __name__ == "__main__":
    main()
