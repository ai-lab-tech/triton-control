"""Train Iris, log its generated Triton model, and deploy it."""

import os

import mlflow
import numpy as np
from mlflow.deployments import get_deploy_client
from mlflow_triton import sklearn
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def deploy_model(model_uri: str) -> dict:
    client = get_deploy_client("triton-control")
    return client.create_deployment(
        name=os.getenv("TRITON_DEPLOYMENT_NAME", "iris-classifier"),
        model_uri=model_uri,
        config={
            "s3_profile_name": os.getenv("TRITON_CONTROL_S3_PROFILE_NAME", "workflow-training"),
            "image": os.getenv("TRITON_SERVING_IMAGE", "nvcr.io/nvidia/tritonserver:26.06-py3"),
            "wait_timeout_seconds": 600,
        },
    )


def main() -> None:
    iris = load_iris(as_frame=True)
    features = iris.data.astype(np.float32)
    features.columns = ["sepal_length", "sepal_width", "petal_length", "petal_width"]
    x_train, x_test, y_train, y_test = train_test_split(
        features, iris.target, test_size=0.2, random_state=42, stratify=iris.target,
    )
    fitted_pipeline = Pipeline([
        ("scaler", StandardScaler()),
        ("classifier", LogisticRegression(max_iter=250, random_state=42)),
    ])
    fitted_pipeline.fit(x_train, y_train)
    example_dataframe = x_test.iloc[:2].copy()

    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "triton-iris-autodeploy"))
    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "iris-autodeploy")):
        mlflow.log_metric("accuracy", float(accuracy_score(y_test, fitted_pipeline.predict(x_test))))


        info = sklearn.log_model(
            fitted_pipeline,
            name="classifier",
            input_example=example_dataframe,
        )

        print(f"Generated model saved in MLflow: {info.model_uri}", flush=True)
        deployment = deploy_model(info.model_uri)
        print(f"Deployment ready: {deployment}", flush=True)


if __name__ == "__main__":
    main()
