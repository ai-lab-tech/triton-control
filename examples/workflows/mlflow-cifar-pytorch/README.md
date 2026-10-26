# CIFAR-10: original PyTorch training with MLflow

This example uses [kuangliu/pytorch-cifar](https://github.com/kuangliu/pytorch-cifar)
at commit `49b7aa97b0c12fe0d4054e670403a16b6b834ddd`. The original architecture files,
`utils.py`, README and license are included unchanged in `upstream/`.
Only `upstream/main.py` is adapted to expose a configurable training function.
`train.py` calls it and provides the MLflow integration.

```text
mlflow-cifar-pytorch/
├── upstream/             # Downloaded original repo; only main.py is adapted
│   ├── main.py
│   ├── utils.py
│   ├── models/
│   └── LICENSE
├── upstream-info.json    # Commit, original checksums and local patch identity
├── upstream.patch        # Changes against the original main.py
├── train.py              # Training configuration and MLflow logging
├── workflow.yaml
└── README.md
```

The workflow trains on CIFAR-10 and logs the final PyTorch model, its Python
sources, inferred requirements, per-epoch metrics and the upstream best
checkpoint. MLflow writes model artifacts to its configured S3 store. This
example prepares the input package for the future generator; it does not yet
generate or deploy a Triton repository.

## Training configuration

The wrapper defaults to **SimpleDLA and one epoch**. It passes the configuration
to `upstream/main.py:run_training()`; no source replacements happen at runtime.
The native function retains 200 epochs as its standalone default.

| Wrapper argument | Default | Purpose |
| --- | --- | --- |
| `--model` | `SimpleDLA` | `SimpleDLA`, `ResNet18`, or `ResNet50`, all original implementations |
| `--epochs` | `1` | Full training passes over CIFAR-10 |
| `--batch-size` | `128` | Training batch size |
| `--test-batch-size` | `100` | Evaluation batch size |
| `--lr` | `0.1` | SGD learning rate |
| `--device` | `auto` | `auto`, `cpu`, or `cuda`; explicit CUDA fails if unavailable |
| `--num-workers` | `2` | Data-loader workers |
| `--data-dir` | `/tmp/cifar-data` | CIFAR-10 download/cache directory |
| `--checkpoint-dir` | `/tmp/cifar-checkpoints` | Directory for `ckpt.pth` |
| `--resume` | disabled | Restore upstream best weights from that checkpoint directory |
| `--seed` | `42` | PyTorch seed |
| `--experiment` | `cifar-pytorch` | MLflow experiment; also configurable with `MLFLOW_EXPERIMENT_NAME` |
| `--registered-model-name` | `cifar10-pytorch` | MLflow registry name |

Resume is a weights-only warm start: as in upstream, optimizer and scheduler state
are not restored. Use a fresh checkpoint directory for a new run; an existing
checkpoint requires `--resume`. The configured epoch count describes additional epochs, and
the cosine schedule runs over that configured training duration.

Training keeps the original random crop, horizontal flip, normalization,
cross-entropy loss, SGD momentum/weight decay and cosine scheduler. The adapted
entry point returns the trained model, metrics and checkpoint path, and calls an
optional epoch callback. It uses plain log lines instead of importing the original
TTY-dependent progress bar. Importing `main.py` does not download data or train.

CIFAR-10 is downloaded at training runtime by torchvision: 50,000 training and
10,000 test images, each RGB 32×32. The files are cached under `--data-dir` and are
not included in the logged model or source archive. A fresh Argo pod downloads
them again unless you mount a persistent cache and point `--data-dir` at it.

## Prepare and upload

1. Enable MLflow and Argo Workflows in Triton Control, with MLflow using an S3
   artifact profile.
2. Link an owned S3 profile under **Workflows → Configure S3 Secrets**. Set the
   workflow annotation `triton-control.ai/mlflow-s3-profile-name` to its name.
   Training-only profile injection must be present in the running backend.
3. The default workflow requests one NVIDIA GPU and uses
   `nvcr.io/nvidia/pytorch:26.06-py3`, which supplies CUDA PyTorch and torchvision.
   The cluster needs an NVIDIA device plugin and a compatible driver. Adjust
   `training-image` to a compatible PyTorch image for your hardware.
4. Allow PyPI access for MLflow installation and network access to the CIFAR-10
   download endpoint used by torchvision. CUDA PyTorch is supplied by the image
   and is not replaced by the workflow.

From the Triton Control repository root, create a source archive:

```bash
tar -czf /tmp/cifar-source.tar.gz \
  -C examples/workflows/mlflow-cifar-pytorch \
  train.py upstream upstream-info.json upstream.patch README.md
```

Upload it with Code-Server's S3 Operations to
`workflows/mlflow-cifar-pytorch/source.tar.gz` in the linked profile's bucket.
Include any profile prefix in the workflow's `s3-source-key`.
The archive must have `train.py` and `upstream/` at its root. Argo downloads and
extracts it into `/workspace/source` before starting the wrapper.

## Run with Argo

Edit `workflow.yaml`:

- Choose an unused `metadata.name` and your linked S3 profile name.
- Set `s3-source-key` to the uploaded archive key.
- Set `model`, `epochs`, `batch-size`, `test-batch-size`, `learning-rate`,
  `device`, `num-workers` and `registered-model-name` as desired.
- For CPU, set `device=cpu` and remove the `nvidia.com/gpu` resource request.
  The configured image must still provide matching CPU-capable torch/torchvision.

Open **Workflows → Submit New Workflow**, upload or paste the edited workflow,
and follow the train logs. Triton Control injects MLflow tracking credentials and
the run creator identity. No personal token or manual artifact upload is needed.

For local use in an environment with PyTorch, torchvision and MLflow installed:

```bash
python train.py --model ResNet18 --epochs 2 --batch-size 64 --device cuda
```

Set `MLFLOW_TRACKING_URI` to your accessible tracking server and provide its normal
authentication before running locally. Configure its S3 artifact store as usual.

## Inspect the result

In experiment `cifar-pytorch`, check per-epoch `train_loss`, `train_accuracy`,
`test_loss`, `test_accuracy` and final `best_test_accuracy`. Accuracies are fractions
between 0 and 1. Parameters and tags record the model configuration and original
repository commit.

Under **Models**, `pytorch-checkpoint` contains:

- The actual final trained network under `data/` and the root `MLmodel` descriptor.
- `code/models/`, original helper source, adapted `main.py`, and wrapper `train.py`.
- MLflow's automatically inferred `requirements.txt`, `conda.yaml` and `python_env.yaml`.
- License, upstream provenance and local patch under `extra_files/`.

The registry contains a new `cifar10-pytorch` version (unless overridden). The
run's `checkpoints/ckpt.pth` retains the best evaluation checkpoint separately;
the logged model is the final network. The workflow returns its
`models:/<model-id>` URI as `mlflow-model-uri`.

The logging step assumes the model's input interface is unknown. It does not
extract an input example, run an additional prediction, infer a signature or
write inference metadata. It saves the model using `serialization_format="pickle"`,
which does not require `input_example`. Training retains its normal evaluation
loop. A future generator can inspect the saved model and code to determine the
serving interface.

Requirements describe MLflow's inferred model environment. The workflow does not
freeze every installed package or infer extra packages for a future Triton backend.
Without an input example, dependency inference observes model loading; dependencies
imported only during prediction may be missed. If inference fails, MLflow falls
back to its default PyTorch requirements.
NVIDIA images can contain vendor/pre-release PyTorch builds; reconstructing their
environment from public PyPI alone may not work. Retain the training-image identity
along with the captured dependency versions when testing generator portability.

## Upstream provenance

`upstream-info.json` records the pinned archive and SHA-256 hashes for every
original file. `upstream.patch` is the complete diff for `main.py`. Reapply it to
a fresh copy of the pinned source from inside its root with:

```bash
patch -p1 < ../upstream.patch
```

Keep the upstream license when redistributing these files. No modifications to
the separate `mlflow-plugin` or `triton-model-generator` repositories are needed
for this training example.
