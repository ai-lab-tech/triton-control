"""Log Iris model, source and model requirements together, then generate serving files."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import mlflow
import mlflow.sklearn
import numpy as np
import onnx
from mlflow.models import Model
from mlflow.tracking import MlflowClient
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FRAMEWORK = "sklearn"
MODEL_NAME = "iris_classifier"
OUTPUT_ROOT = Path(os.getenv("TRITON_REPOSITORY_DIR", "/tmp/model-repository"))


# Simulates generator
def simulate_triton_repository_generation(model_uri: str, output_model_id: str) -> str:
    """Simulate generation from a complete saved MLflow sklearn model directory.

    Download model files, code and MLflow-inferred requirements together. Load the
    saved model rather than reuse the training process's in-memory model. The
    Iris-specific ONNX conversion and configuration are predefined here; no source
    analysis or new dependency inference is simulated. Upload the finished
    repository to the allocated logged model's artifact store and return its S3 URI.
    The simulation uses MLflow's artifact proxy for transfer. The caller owns
    the logged model lifecycle and registry version.
    """
    bundle = Path(mlflow.artifacts.download_artifacts(artifact_uri=model_uri))
    code_artifacts = bundle / "code"
    code_files = sorted(
        str(path.relative_to(code_artifacts)) for path in code_artifacts.rglob("*.py")
    )
    if not code_artifacts.is_dir() or not code_files:
        raise ValueError("The logged model must include training source via code_paths")
    requirements_file = bundle / "requirements.txt"
    requirements = requirements_file.read_text(encoding="utf-8").splitlines()
    if not requirements:
        raise ValueError("The model's requirements.txt is empty")
    model_root = OUTPUT_ROOT / MODEL_NAME
    version_dir = model_root / "1"

    def _triton_config(label_output: str, probability_output: str) -> str:
        return f'''name: "{MODEL_NAME}"
platform: "onnxruntime_onnx"
max_batch_size: 0
input [
  {{
    name: "input"
    data_type: TYPE_FP32
    dims: [ -1, 4 ]
  }}
]
output [
  {{
    name: "{label_output}"
    data_type: TYPE_INT64
    dims: [ -1 ]
  }},
  {{
    name: "{probability_output}"
    data_type: TYPE_FP32
    dims: [ -1, 3 ]
  }}
]
'''

    version_dir.mkdir(parents=True, exist_ok=True)
    trained_raw_model = mlflow.sklearn.load_model(str(bundle))
    onnx_model = convert_sklearn(
        trained_raw_model,
        initial_types=[("input", FloatTensorType([None, 4]))],
        options={id(trained_raw_model.named_steps["classifier"]): {"zipmap": False}},
        target_opset=17,
    )
    onnx.save_model(onnx_model, version_dir / "model.onnx")
    source_flavor = "sklearn"
    onnx.checker.check_model(onnx_model)
    output_names = [value.name for value in onnx_model.graph.output]
    if len(output_names) != 2:
        raise RuntimeError(
            f"Expected label and probability outputs, got {output_names}"
        )
    shutil.copy2(requirements_file, model_root / "requirements.txt")
    (model_root / "config.pbtxt").write_text(
        _triton_config(output_names[0], output_names[1]),
        encoding="utf-8",
    )
    (model_root / "training-metadata.json").write_text(
        json.dumps(
            {
                "training_code_files": code_files,
                "model_requirements": requirements,
                "source_model_uri": model_uri,
                "generator": "simulated-iris-onnx",
                "source_flavor": source_flavor,
                "onnx_outputs": output_names,
                "workflow": os.getenv("ARGO_WORKFLOW_NAME", ""),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    client = MlflowClient()
    artifact_uri = (
        client.get_logged_model(output_model_id).artifact_location.rstrip("/")
        + "/"
        + MODEL_NAME
    )
    parsed = urlsplit(artifact_uri)
    if parsed.scheme == "s3":
        repository_uri = artifact_uri
    elif parsed.scheme == "mlflow-artifacts" and not parsed.netloc:
        # Simulation transport: reuse the managed artifact proxy so no S3 keys
        # need to be embedded in the example or its workflow manifest.
        response = httpx.get(
            mlflow.get_tracking_uri().rstrip("/")
            + "/api/2.0/mlflow/triton-control/artifact-store",
            headers={"Authorization": "Bearer " + os.environ["MLFLOW_TRACKING_TOKEN"]},
            timeout=30,
            follow_redirects=False,
        )
        response.raise_for_status()
        store = response.json()["artifact_root"].rstrip("/")
        parts = unquote(parsed.path).strip("/").split("/")
        if (
            urlsplit(store).scheme != "s3"
            or parsed.query
            or parsed.fragment
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("The generator requires a valid S3-backed artifact store")
        repository_uri = store + "/" + "/".join(parts)
    else:
        raise ValueError(
            "The generator simulation requires an S3-backed artifact store"
        )
    # Upload the generated model-name directory once into the reserved model.
    with tempfile.TemporaryDirectory() as temp:
        shutil.copytree(model_root, Path(temp) / MODEL_NAME)
        client.log_model_artifacts(output_model_id, temp)
    return repository_uri


def generate_triton_repo(model_uri: str, registered_model_name: str):
    """Simulate the future plugin's MLflow orchestration.

    1. Create a Logged Model.
    2. Call the generator to create and upload the Triton repository.
    3. Add the MLmodel descriptor to the logged model's artifact directory.
    4. Set the status to READY and register the model.
    """

    run = mlflow.active_run()
    if run is None:
        raise RuntimeError("generate_triton_repo requires an active MLflow run")
    client = MlflowClient()
    logged_model = client.create_logged_model(
        experiment_id=run.info.experiment_id,
        name="triton-repository",
        source_run_id=run.info.run_id,
        model_type="triton",
    )
    try:
        repository_uri = simulate_triton_repository_generation(
            model_uri, output_model_id=logged_model.model_id
        )
        descriptor = Model(run_id=run.info.run_id, model_id=logged_model.model_id)
        descriptor.add_flavor("triton", data=MODEL_NAME, model_name=MODEL_NAME)
        with tempfile.TemporaryDirectory() as temp:
            descriptor.save(str(Path(temp) / "MLmodel"))
            client.log_model_artifacts(logged_model.model_id, temp)
        client.set_logged_model_tags(
            logged_model.model_id, {"triton_model_uri": repository_uri}
        )
        client.finalize_logged_model(logged_model.model_id, "READY")
    except Exception:
        client.finalize_logged_model(logged_model.model_id, "FAILED")
        raise
    return mlflow.register_model(
        model_uri=f"models:/{logged_model.model_id}",
        name=registered_model_name,
        tags={"triton_model_uri": repository_uri},
    )


def main() -> None:

    ###################
    ### train model ###
    ###################

    iris = load_iris()
    features = iris.data.astype(np.float32)
    labels = iris.target
    x_train, x_test, y_train, y_test = train_test_split(
        features,
        labels,
        test_size=0.2,
        random_state=42,
        stratify=labels,
    )
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=250, random_state=42)),
        ]
    )
    model.fit(x_train, y_train)
    predictions = model.predict(x_test)
    training_params = {
        "model": "StandardScaler + LogisticRegression",
        "max_iter": 250,
    }
    accuracy = float(accuracy_score(y_test, predictions))

    ########################
    ### Start MLflow run ###
    ########################
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "triton-autodeploy"))
    raw_registry_name = os.getenv(
        "MLFLOW_SKLEARN_REGISTERED_MODEL_NAME", "iris-classifier-sklearn"
    )
    triton_registry_name = os.getenv(
        "MLFLOW_TRITON_REGISTERED_MODEL_NAME", "iris-classifier-triton"
    )

    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "iris-autodeploy")):
        mlflow.log_params(
            {
                **training_params,
                "framework": FRAMEWORK,
                "test_size": 0.2,
                "random_state": 42,
            }
        )
        mlflow.log_metric("accuracy", accuracy)
        mlflow.set_tags(
            {
                "argo.workflow.name": os.getenv("ARGO_WORKFLOW_NAME", ""),
                "argo.workflow.uid": os.getenv("ARGO_WORKFLOW_UID", ""),
                "deployment.target": "triton-control",
            }
        )

        ######################
        ### log raw model ###
        ######################
        code_paths = [str(Path(__file__).resolve())]
        raw_model_info = mlflow.sklearn.log_model(
            sk_model=model,
            name=f"{FRAMEWORK}-checkpoint",
            code_paths=code_paths,
            registered_model_name=raw_registry_name,
        )

        ######################
        ### call generator ###
        ######################
        triton_version = generate_triton_repo(
            model_uri=raw_model_info.model_uri,
            registered_model_name=triton_registry_name,
        )
        repository_uri = triton_version.tags["triton_model_uri"]
        Path(os.environ["TRITON_MODEL_URI_OUTPUT"]).write_text(
            repository_uri, encoding="utf-8"
        )

        #### 
        # deploy 

    print(
        json.dumps(
            {
                "accuracy": accuracy,
                "framework": FRAMEWORK,
                "raw_registered_model": raw_registry_name,
                "raw_registered_model_version": raw_model_info.registered_model_version,
                "triton_repository_uri": repository_uri,
                "triton_registered_model": triton_registry_name,
                "triton_registered_model_version": triton_version.version,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
