# Automatic Triton Versioning During Separate Model Registration

Each execution trains the Iris pipeline exactly once and then performs two steps:

1. `sklearn.log_model(..., name="automatic-triton-versions-register")` logs the
   model without a `registered_model_name` argument.
2. `mlflow_triton.register_model(info.model_uri, registered_model_name)` registers
   it with `registered_model_name = "automatic-triton-versions-register"`.

There is no retraining. The script deploys `models:/{version.name}/{version.version}`
from the registration result. Each registration creates a new Registry version with its own native weights.
For a fresh unregistered model, registration retains its model ID and may
renumber its Triton directory; this requires artifact deletion permission.
Use the plugin's `register_model`, which implements the Triton version rules.

## Plugin wheels

This example installs the published `mlflow-triton==0.4.6` and
`mlflow-triton-control==0.4.6` wheels directly from PyPI.
Only the training script needs to be uploaded to S3.

## Prepare and run through Argo

1. Enable MLflow with an S3 artifact store and Argo Workflows in Triton Control.
2. Link the same S3 profile under **Workflows → Configure S3 Secrets**.
3. Upload `train.py` to `workflows/mlflow-iris-auto-versioning-on-register/train.py`.
4. In `workflow.yaml`, set `s3-script-key` to the full uploaded script key,
   including any profile prefix.
   Choose unused workflow and deployment names. The default deployment name is
   `automatic-triton-versions-register`.
5. If multiple S3 profiles are linked, add the annotation
   `triton-control.ai/mlflow-s3-profile-name` with the selected profile name.
6. In **Workflows → Submit New Workflow → Edit using full workflow options**,
   submit the complete `workflow.yaml`, including `spec.arguments.parameters`.

Argo downloads the script as an S3 input artifact. The training container
installs both plugin wheels and the pinned training dependencies from PyPI. Deployment runs inside `train.py`.

## Verify

Wait for **train → Succeeded**. The logs show the logged and registered model URIs,
example predictions, and the ready deployment.

In MLflow, open the `triton-iris-autodeploy` experiment and this workflow's run.
The logged model and registered model are both named `automatic-triton-versions-register`.
The Triton artifact directory is `model_repository/automatic-triton-versions-register/`.
Release 0.4.6 creates a new MLflow Registry version for each registration.
Compatible models append a numeric Triton version to the first model's shared
repository; each new logged model also keeps its own serving copy. Native
weights of earlier registered models remain unchanged. If `config.pbtxt` changes,
a new repository starts at `1/`. Registry and Triton versions can therefore differ.

Open the deployment's **Models → automatic-triton-versions-register → Infer** and use the
generated `request.json` from the model artifacts. Expected output: `[0, 2]`.

## Run in code-server

Copy `train.py` into a workspace directory. From that directory:

```bash
python3 -m pip install --user \
  "mlflow-triton==0.4.6" \
  "mlflow-triton-control==0.4.6" \
  "mlflow==3.14.0" "numpy==2.2.6" "pandas==2.3.3" \
  "scikit-learn==1.7.2" "cloudpickle==3.1.2"
export TRITON_DEPLOYMENT_NAME="automatic-triton-versions-register-manual"
export TRITON_CONTROL_S3_PROFILE_NAME="YOUR_MLFLOW_S3_PROFILE"
python3 train.py
```

The workspace supplies MLflow and Triton Control connection settings. Use an unused
deployment name for each run. The default serving image is
`nvcr.io/nvidia/tritonserver:26.06-py3`; the readiness timeout is 1800 seconds.
