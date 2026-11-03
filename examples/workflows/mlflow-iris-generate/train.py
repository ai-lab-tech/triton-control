"""Train Iris and generate a Triton model in the MLflow artifact store."""

import os

import mlflow
import numpy as np
from mlflow_triton import sklearn
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


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

    mlflow.set_experiment(os.getenv("MLFLOW_EXPERIMENT_NAME", "triton-iris-generate"))
    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "iris-generate")):
        mlflow.log_metric("accuracy", float(accuracy_score(y_test, fitted_pipeline.predict(x_test))))
        info = sklearn.log_model(
            fitted_pipeline,
            name="classifier",
            input_example=example_dataframe,
        )
        print(f"Generated model saved in MLflow: {info.model_uri}", flush=True)


if __name__ == "__main__":
    main()
