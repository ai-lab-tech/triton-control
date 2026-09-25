"""Train an Iris classifier, track it in MLflow, and export a Triton repository."""

from __future__ import annotations

import json
import os
from pathlib import Path

import mlflow
import mlflow.sklearn
import numpy as np
import onnx
from mlflow.models import infer_signature
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MODEL_NAME = "iris_classifier"
OUTPUT_ROOT = Path(os.getenv("TRITON_REPOSITORY_DIR", "/tmp/model-repository"))


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


def main() -> None:
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
    accuracy = float(accuracy_score(y_test, predictions))

    tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "triton-autodeploy"))
    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "iris-autodeploy")):
        mlflow.log_params(
            {
                "model": "StandardScaler + LogisticRegression",
                "test_size": 0.2,
                "random_state": 42,
                "max_iter": 250,
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
        mlflow.sklearn.log_model(
            sk_model=model,
            name="model",
            signature=infer_signature(x_test, predictions),
            input_example=x_test[:2],
            registered_model_name=os.getenv("MLFLOW_REGISTERED_MODEL_NAME", "iris-classifier"),
        )

    onnx_model = convert_sklearn(
        model,
        initial_types=[("input", FloatTensorType([None, 4]))],
        options={id(model.named_steps["classifier"]): {"zipmap": False}},
        target_opset=17,
    )
    onnx.checker.check_model(onnx_model)
    output_names = [value.name for value in onnx_model.graph.output]
    if len(output_names) != 2:
        raise RuntimeError(f"Expected label and probability outputs, got {output_names}")

    model_root = OUTPUT_ROOT / MODEL_NAME
    version_dir = model_root / "1"
    version_dir.mkdir(parents=True, exist_ok=True)
    onnx.save_model(onnx_model, version_dir / "model.onnx")
    (model_root / "config.pbtxt").write_text(
        _triton_config(output_names[0], output_names[1]),
        encoding="utf-8",
    )
    (model_root / "training-metadata.json").write_text(
        json.dumps(
            {
                "accuracy": accuracy,
                "onnx_outputs": output_names,
                "workflow": os.getenv("ARGO_WORKFLOW_NAME", ""),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"accuracy": accuracy, "triton_model": str(model_root)}, indent=2))


if __name__ == "__main__":
    main()
