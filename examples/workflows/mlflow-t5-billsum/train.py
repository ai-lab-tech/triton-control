"""Fine-tune T5 with the original Hugging Face script, then log its components."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import mlflow
import mlflow.transformers
import torch
import torchvision
from datasets import load_dataset
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

ROOT = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="google-t5/t5-small")
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--eval-batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=5e-5)
    parser.add_argument("--max-source-length", type=int, default=512)
    parser.add_argument("--max-target-length", type=int, default=128)
    parser.add_argument("--max-train-samples", type=int, default=0)
    parser.add_argument("--max-eval-samples", type=int, default=0)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="cuda")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--work-dir", type=Path, default=Path("/tmp/t5-billsum-training")
    )
    parser.add_argument(
        "--experiment", default=os.getenv("MLFLOW_EXPERIMENT_NAME", "t5-billsum")
    )
    parser.add_argument("--registered-model-name", default="t5-billsum")
    args = parser.parse_args()
    positive = (
        args.epochs,
        args.batch_size,
        args.eval_batch_size,
        args.learning_rate,
        args.max_source_length,
        args.max_target_length,
    )
    if any(value <= 0 for value in positive):
        parser.error(
            "Epochs, batch sizes, learning rate and sequence lengths must be positive"
        )
    if args.max_train_samples < 0 or args.max_eval_samples < 0:
        parser.error("Sample limits must be >= 0 (0 uses the complete split)")
    if args.device == "cuda" and not torch.cuda.is_available():
        parser.error("CUDA was requested, but no CUDA GPU is available")
    return args


def main():
    args = parse_args()
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()):
        raise FileExistsError(f"Choose an empty --work-dir: {work}")
    work.mkdir(parents=True, exist_ok=True)
    checkpoint = work / "checkpoint"
    provenance = json.loads((ROOT / "upstream-info.json").read_text())
    mlflow.set_experiment(args.experiment)

    with mlflow.start_run(run_name=os.getenv("ARGO_WORKFLOW_NAME", "t5-billsum")):
        dataset = load_dataset("FiscalNote/billsum", split="ca_test")
        splits = dataset.train_test_split(test_size=0.2, seed=args.seed)
        files = {}
        counts = {}
        for split, limit in (
            ("train", args.max_train_samples),
            ("test", args.max_eval_samples),
        ):
            rows = splits[split].select_columns(["text", "summary"])
            if limit:
                rows = rows.select(range(min(limit, len(rows))))
            files[split] = work / f"{split}.json"
            rows.to_json(str(files[split]))
            counts[split] = len(rows)

        mlflow.log_params(
            {
                "model": args.model,
                "torch_version": torch.__version__,
                "torchvision_version": torchvision.__version__,
                "dataset": "FiscalNote/billsum",
                "dataset_split": "ca_test",
                "epochs": args.epochs,
                "batch_size": args.batch_size,
                "eval_batch_size": args.eval_batch_size,
                "learning_rate": args.learning_rate,
                "max_source_length": args.max_source_length,
                "max_target_length": args.max_target_length,
                "train_samples": counts["train"],
                "eval_samples": counts["test"],
                "seed": args.seed,
                "device": args.device,
            }
        )
        mlflow.set_tags(
            {
                "upstream.repository": provenance["repository"],
                "upstream.revision": provenance["revision"],
                "training.image": os.getenv("TRAINING_IMAGE", ""),
                "argo.workflow.name": os.getenv("ARGO_WORKFLOW_NAME", ""),
            }
        )
        command = [
            sys.executable,
            str(ROOT / "upstream/run_summarization.py"),
            "--model_name_or_path",
            args.model,
            "--train_file",
            str(files["train"]),
            "--validation_file",
            str(files["test"]),
            "--text_column",
            "text",
            "--summary_column",
            "summary",
            "--source_prefix",
            "summarize: ",
            "--output_dir",
            str(checkpoint),
            "--do_train",
            "--do_eval",
            "--eval_strategy",
            "epoch",
            "--num_train_epochs",
            str(args.epochs),
            "--per_device_train_batch_size",
            str(args.batch_size),
            "--per_device_eval_batch_size",
            str(args.eval_batch_size),
            "--learning_rate",
            str(args.learning_rate),
            "--max_source_length",
            str(args.max_source_length),
            "--max_target_length",
            str(args.max_target_length),
            "--seed",
            str(args.seed),
            "--data_seed",
            str(args.seed),
            "--save_strategy",
            "no",
            "--save_safetensors",
            "true",
            "--predict_with_generate",
            "false",
            "--report_to",
            "none",
            "--logging_steps",
            "10",
            "--dataloader_num_workers",
            "0",
            "--push_to_hub",
            "false",
        ]
        if args.device == "cpu":
            command.extend(["--use_cpu", "true"])
        subprocess.run(command, cwd=work, check=True)

        metrics = json.loads((checkpoint / "all_results.json").read_text())
        mlflow.log_metrics(
            {
                name: value
                for name, value in metrics.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        mlflow.log_artifact(
            str(ROOT / "upstream-info.json"), artifact_path="provenance"
        )
        mlflow.log_artifact(
            str(ROOT / "requirements-training.txt"), artifact_path="provenance"
        )

        # Log components without executing a prediction or supplying an input example.
        model = AutoModelForSeq2SeqLM.from_pretrained(checkpoint).eval()
        tokenizer = AutoTokenizer.from_pretrained(checkpoint)
        
        info = mlflow.transformers.log_model(
            transformers_model={"model": model, "tokenizer": tokenizer},
            name="transformers-checkpoint",
            task="summarization",
            save_pretrained=True,
            code_paths=[str(ROOT / "train.py"), str(ROOT / "upstream")],
            registered_model_name=args.registered_model_name,
        )
        output = os.getenv("MLFLOW_MODEL_URI_OUTPUT")
        if output:
            Path(output).write_text(info.model_uri, encoding="utf-8")
        print(
            json.dumps(
                {
                    "model_uri": info.model_uri,
                    "registered_model_name": args.registered_model_name,
                    "registered_model_version": info.registered_model_version,
                    "metrics": metrics,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
