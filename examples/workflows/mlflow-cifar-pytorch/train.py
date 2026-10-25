"""Run upstream CIFAR-10 training and log its model, code and inferred requirements."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

import mlflow
import mlflow.pytorch
import torch
from mlflow.models import infer_signature

ROOT = Path(__file__).resolve().parent
UPSTREAM = ROOT / "upstream"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--test-batch-size", type=int, default=100)
    parser.add_argument("--lr", type=float, default=0.1)
    parser.add_argument(
        "--model", choices=("SimpleDLA", "ResNet18", "ResNet50"), default="SimpleDLA"
    )
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--data-dir", type=Path, default=Path("/tmp/cifar-data"))
    parser.add_argument(
        "--checkpoint-dir", type=Path, default=Path("/tmp/cifar-checkpoints")
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--experiment", default=os.getenv("MLFLOW_EXPERIMENT_NAME", "cifar-pytorch")
    )
    parser.add_argument("--registered-model-name", default="cifar10-pytorch")
    return parser.parse_args()


def load_training_module():
    """Import the configured entry point; importing it does not start training."""
    sys.path.insert(0, str(UPSTREAM))
    spec = importlib.util.spec_from_file_location(
        "cifar_training", UPSTREAM / "main.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    args = parse_args()
    training = load_training_module()
    provenance = json.loads((ROOT / "upstream-info.json").read_text())
    torch.manual_seed(args.seed)
    mlflow.set_experiment(args.experiment)
    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "cifar-pytorch")):
        mlflow.log_params(
            {
                "model": args.model,
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "test_batch_size": args.test_batch_size,
                "learning_rate": args.lr,
                "device": args.device,
                "num_workers": args.num_workers,
                "seed": args.seed,
                "resume": args.resume,
            }
        )
        mlflow.set_tags(
            {
                "upstream.repository": provenance["repository"],
                "upstream.commit": provenance["commit"],
                "upstream.local_patch": "upstream.patch",
                "training.image": os.getenv("TRAINING_IMAGE", ""),
                "argo.workflow.name": os.getenv("ARGO_WORKFLOW_NAME", ""),
            }
        )
        result = training.run_training(
            epochs=args.epochs,
            batch_size=args.batch_size,
            test_batch_size=args.test_batch_size,
            lr=args.lr,
            model_name=args.model,
            data_dir=args.data_dir,
            checkpoint_dir=args.checkpoint_dir,
            device=args.device,
            num_workers=args.num_workers,
            resume=args.resume,
            on_epoch_end=lambda epoch, metrics: mlflow.log_metrics(
                metrics, step=epoch + 1
            ),
        )
        # Save the actual final network; the upstream best checkpoint is retained separately.
        model = result["model"]
        if isinstance(model, torch.nn.DataParallel):
            model = model.module
        model = model.cpu().eval()
        input_example = result["input_example"].cpu()
        with torch.no_grad():
            predictions = model(input_example).numpy()
        metadata = {
            "classes": result["classes"],
            "input": "normalized FP32 NCHW tensors, shape (batch, 3, 32, 32)",
            "normalization": {
                "mean": [0.4914, 0.4822, 0.4465],
                "std": [0.2023, 0.1994, 0.2010],
                "included_in_model_forward": False,
            },
            "output": "ten class logits in the documented class order",
        }
        with tempfile.TemporaryDirectory() as temp:
            metadata_file = Path(temp) / "inference-metadata.json"
            metadata_file.write_text(json.dumps(metadata, indent=2))
            info = mlflow.pytorch.log_model(
                pytorch_model=model,
                name="pytorch-checkpoint",
                serialization_format="pickle",
                code_paths=[
                    str(UPSTREAM / "models"),
                    str(UPSTREAM / "main.py"),
                    str(UPSTREAM / "utils.py"),
                    str(Path(__file__).resolve()),
                ],
                extra_files=[
                    str(metadata_file),
                    str(UPSTREAM / "LICENSE"),
                    str(ROOT / "upstream-info.json"),
                    str(ROOT / "upstream.patch"),
                ],
                input_example=input_example.numpy(),
                signature=infer_signature(input_example.numpy(), predictions),
                registered_model_name=args.registered_model_name,
            )
        mlflow.log_metrics(result["metrics"])
        mlflow.log_artifact(str(result["checkpoint"]), artifact_path="checkpoints")
        output_file = os.getenv("MLFLOW_MODEL_URI_OUTPUT")
        if output_file:
            Path(output_file).write_text(info.model_uri, encoding="utf-8")
        print(
            json.dumps(
                {
                    "model_uri": info.model_uri,
                    "registered_model_name": args.registered_model_name,
                    "registered_model_version": info.registered_model_version,
                    "metrics": result["metrics"],
                    "upstream_commit": provenance["commit"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
