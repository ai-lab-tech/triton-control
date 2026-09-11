import argparse
import json
from pathlib import Path

import joblib
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a scikit-learn Iris classifier.")
    parser.add_argument("--test-size", type=float, default=0.2)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/outputs"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    iris = load_iris()
    x_train, x_test, y_train, y_test = train_test_split(
        iris.data,
        iris.target,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=iris.target,
    )

    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(max_iter=args.max_iter, random_state=args.random_state),
            ),
        ]
    )
    model.fit(x_train, y_train)

    predictions = model.predict(x_test)
    accuracy = accuracy_score(y_test, predictions)
    metrics = {
        "dataset": "sklearn.datasets.load_iris",
        "model": "StandardScaler + LogisticRegression",
        "test_size": args.test_size,
        "random_state": args.random_state,
        "max_iter": args.max_iter,
        "accuracy": accuracy,
        "target_names": iris.target_names.tolist(),
        "classification_report": classification_report(
            y_test,
            predictions,
            target_names=iris.target_names,
            output_dict=True,
        ),
    }

    joblib.dump(model, args.output_dir / "iris-logreg-model.joblib")
    (args.output_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "accuracy.txt").write_text(f"{accuracy:.6f}\n", encoding="utf-8")
    (args.output_dir / "labels.txt").write_text(
        "\n".join(iris.target_names.tolist()) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
