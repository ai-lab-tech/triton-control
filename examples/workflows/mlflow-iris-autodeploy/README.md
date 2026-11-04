# Train, Generate and Deploy Iris

A single `train.py` trains an Iris classifier, logs it with its generated Triton
files, then deploys the returned MLflow model URI:

```python
from mlflow_triton import sklearn

info = sklearn.log_model(
    fitted_pipeline,
    name="classifier",
    input_example=example_dataframe,
)
deployment = deploy_model(info.model_uri)
```

`sklearn.log_model()` comes from our [MLflow Triton plugin](https://github.com/ai-lab-tech/mlflow-plugin).
The `mlflow-triton` core generates the Triton files and logs the model to MLflow;
`mlflow-triton-control` adds deployment to Triton Control.

`deploy_model()` contains the `get_deploy_client("triton-control")` and
`create_deployment()` calls. The plugin reads the generated `requirements.txt`
automatically. The model is saved in the MLflow artifact store; no Model Registry
registration or alias management is used.

## Prepare

1. Use the updated Triton Control backend and enable MLflow with S3 storage and
   Argo Workflows.
2. Save an S3 profile and link it under **Workflows → Configure S3 Secrets**.
   It must read both the workflow inputs and the MLflow artifact bucket.
3. Upload only `train.py` with code-server's S3 browser to
   `workflows/mlflow-iris-autodeploy/train.py`. Include any profile prefix in the
   workflow's `s3-script-key`.

The workflow installs `mlflow-triton-control` directly from PyPI. Its matching
`mlflow-triton` core package is installed automatically.

## Run through Argo

Edit [workflow.yaml](workflow.yaml):

| Setting | Default |
| --- | --- |
| `metadata.name` | `mlflow-iris-autodeploy`; choose an unused workflow name |
| Parameter `deployment-name` | `iris-classifier`; choose an unused deployment name |
| Optional annotation `triton-control.ai/mlflow-s3-profile-name` | Only needed to select a profile when multiple profiles are linked |
| `s3-script-key` | Uploaded training script's full S3 key |
| `triton-image` | `nvcr.io/nvidia/tritonserver:26.06-py3` |

With exactly one of your S3 profiles linked under **Workflows → Configure S3
Secrets** in the workflow namespace, no profile annotation is needed. Triton
Control selects it automatically.

**With multiple linked profiles, you must select one by name.** Add the annotation
to the existing `metadata` block in `workflow.yaml`, replacing `workflow-training`
with the saved profile name:

```yaml
metadata:
  name: mlflow-iris-autodeploy
  annotations:
    triton-control.ai/mlflow-s3-profile-name: workflow-training
```

Submit through **Triton Control → Workflows → Submit New Workflow**. The workflow
has one **train** node that installs packages and runs `train.py`. Deployment
happens inside that script, not in a separate Argo step.

## Verify

Wait for **train → Succeeded**. The logs print the MLflow model URI and the ready
deployment. If deployment fails, the script and workflow fail.

In **MLflow**, select **Model training**, open the **triton-iris-autodeploy**
experiment, then your run and its logged model **classifier**. The model may open
in a new browser tab. On its **Artifacts** tab, expand `model_repository/classifier`:

```text
model_repository/classifier/
  config.pbtxt
  requirements.txt
  request.json
  validation.json
  1/model.py
  1/artifact/
```

Open **Triton Instances → your deployment → Details → Models → classifier →
Version 1 → Infer**. Use the name selected in the `deployment-name` parameter
(default: `iris-classifier`). Paste the generated `request.json` from MLflow and
click **Send / Infer**. It contains the four named Iris input tensors and the
correct output name. The unmodified example request returns `[0, 2]`.

## Run the same script in code-server

Install the plugin directly from PyPI plus the same training dependencies listed
in `workflow.yaml` in your Python environment:

```bash
python -m pip install --upgrade --index-url https://pypi.org/simple mlflow-triton-control \
  "mlflow==3.14.0" "numpy==2.2.6" "pandas==2.3.3" \
  "scikit-learn==1.7.2" "cloudpickle==3.1.2"
```

A managed workspace with the updated backend supplies tracking and deployment
credentials automatically; existing workspaces need the workspace upgrade or recreation.

```bash
export TRITON_DEPLOYMENT_NAME="iris-classifier-manual"
export TRITON_CONTROL_S3_PROFILE_NAME="workflow-training"
python train.py
```

The serving image defaults to `nvcr.io/nvidia/tritonserver:26.06-py3`; override it
with `TRITON_SERVING_IMAGE`. The deployment readiness timeout is 600 seconds.
Use a new deployment name for each run; the plugin creates deployments rather
than updating existing ones.
