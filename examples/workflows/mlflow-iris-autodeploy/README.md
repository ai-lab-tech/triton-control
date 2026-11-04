# Train, Generate and Deploy Iris

A single `train.py` trains an Iris classifier, logs it with its generated Triton
files, then deploys the returned MLflow model URI:

```python
info = sklearn.log_model(
    fitted_pipeline,
    name="classifier",
    input_example=example_dataframe,
)
deployment = deploy_model(info.model_uri)
```

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
`mlflow-triton` core package is installed automatically. No wheel build or package
upload is needed. Use a published plugin release supporting the current deployment
API and automatic serving requirements.

## Run through Argo

Edit [workflow.yaml](workflow.yaml):

| Setting | Default |
| --- | --- |
| `metadata.name` | `mlflow-iris-autodeploy`; choose an unused workflow name |
| Annotation `triton-control.ai/mlflow-deploy-template` | `train` |
| Annotation `triton-control.ai/mlflow-deployment-name` | `iris-classifier`; choose an unused deployment name |
| Annotation `triton-control.ai/mlflow-s3-profile-name` | `workflow-training`; replace with your linked profile name, or omit when exactly one of your profiles is linked |
| `s3-script-key` | Uploaded training script's full S3 key |
| `triton-image` | `nvcr.io/nvidia/tritonserver:26.06-py3` |

The backend fills in `artifactRepositoryRef` from the selected linked profile.
You do not need ConfigMap or Secret names in the workflow YAML. With multiple linked
profiles, keep the profile-name annotation to select one explicitly.

Submit through **Triton Control → Workflows → Submit New Workflow**. The workflow
has one **train** node that installs packages and runs `train.py`. Deployment
happens inside that script, not in a separate Argo step.

Triton Control injects the backend URL, tracking credentials, deployment token
and S3 profile name. The workflow supplies deployment name and serving image as
environment variables. No profile IDs or manually copied tokens are needed.
Workflow and serving pods need access to the package index; dependencies are
installed before Triton starts.

## Verify

Wait for **train → Succeeded**. The logs print the MLflow model URI and the ready
deployment. If deployment fails, the script and workflow fail.

In **MLflow → triton-iris-autodeploy**, open the run and logged model **classifier**:

```text
model_repository/classifier/
  config.pbtxt
  requirements.txt
  request.json
  validation.json
  1/model.py
  1/artifact/
```

Open **Triton Instances → iris-classifier → Models → classifier → Version 1 →
Infer** and paste the generated `request.json` from MLflow. It contains the four
named Iris input tensors and the correct output name.

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
