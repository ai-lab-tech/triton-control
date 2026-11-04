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

Use the included [train.py](train.py), which already defines `deploy_model()`.
Follow the steps below to upload the script and run the training and deployment.

## Prepare

1. In Triton Control, enable **MLflow** with an S3 artifact store and enable
   **Argo Workflows**.
2. Open **Triton Control → MLflow**. Below the page title, check **S3 profile**
   for the profile name and **Artifacts** for its S3 path.
   Link that profile under **Triton Control → Workflows → Configure S3 Secrets**
   and use it when uploading the training script in step 4.

   > [!WARNING]
   > The profile's bucket must match the bucket shown in MLflow's **Artifacts**.
   > Using a different bucket lets training finish, but deployment fails.

3. Follow [Create a Development Workspace](../sklearn-iris-training/README.md#1-create-a-development-workspace).
   When the workspace is ready, open **code-server** from **Development**.
   Create `/workspace/mlflow-iris-autodeploy/` and copy this example's
   [train.py](train.py) and [workflow.yaml](workflow.yaml) into it.
4. Follow [Upload the Training Script](../sklearn-iris-training/README.md#3-upload-the-training-script)
   to upload this example's `train.py` to
   `workflows/mlflow-iris-autodeploy/train.py`. Set the workflow's `s3-script-key`
   to that full S3 key, including any profile prefix.

The workflow installs `mlflow-triton-control` directly from PyPI. Its matching
`mlflow-triton` core package is installed automatically.

## Run through Argo

In **code-server**, open `/workspace/mlflow-iris-autodeploy/workflow.yaml`
and edit these settings:

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

Open **Triton Control → Workflows → Submit New Workflow → Edit using full workflow options**.
Paste the entire edited `workflow.yaml`, including `spec.arguments.parameters`,
then create the workflow.

The workflow has one **train** node that installs packages and runs `train.py`. Deployment
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

Open **Triton Instances → your deployment → Details → Models → classifier
(Version 1) → Infer**. Use the name selected in the `deployment-name` parameter
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
