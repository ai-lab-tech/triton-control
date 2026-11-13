# Update, Generate and Deploy Iris

Each execution creates a fresh logged model and runs the complete workflow:

1. Train an Iris pipeline and log it normally: Triton folder `1/`.
2. Keep the trained pipeline in memory and retain `first.model_uri`.
3. Change `C` from `1.0` to `0.5` and train the same pipeline again.
4. Log the update with `model_uri=first.model_uri`, without registration.
5. Print example predictions from the retrained pipeline.
6. Deploy the updated model (version 2).

No existing model URI is needed. Repeating the example creates a new model with two versions,
instead of extending a previous test model to versions 3 or 4. A failed deployment
leaves the already logged model available for inspection.

Use the included [train.py](train.py) and [workflow.yaml](workflow.yaml).
The workflow installs the published `mlflow-triton==0.4.5` and
`mlflow-triton-control==0.4.5` wheels directly from PyPI.

## Prepare

1. In Triton Control, enable **MLflow** with an S3 artifact store and enable
   **Argo Workflows**.
2. Under **Workflows → Configure S3 Secrets**, link the S3 profile marked
   **MLflow artifact store**. Select the same marked profile in code-server when
   uploading `train.py`. Profiles are global; each user creates their own Argo link.

3. Follow [Create a Development Workspace](../sklearn-iris-training/README.md#1-create-a-development-workspace).
   When the workspace is ready, open **code-server** from **Development**.
   Create `/workspace/mlflow-iris-update-autodeploy/` and copy this example's
   [train.py](train.py) and [workflow.yaml](workflow.yaml) into it.
4. Follow [Upload the Training Script](../sklearn-iris-training/README.md#3-upload-the-training-script)
   to upload this example's `train.py` to
   `workflows/mlflow-iris-update-autodeploy/train.py`. Set the workflow's `s3-script-key`
   to that full S3 key, including any profile prefix.

The workflow downloads `train.py` through an Argo S3 input artifact and installs
both plugin wheels directly from PyPI. No plugin files need to be uploaded to S3.
MLflow and the training dependencies are also installed from PyPI.

## Run through Argo

In **code-server**, open `/workspace/mlflow-iris-update-autodeploy/workflow.yaml`
and edit these settings:

| Setting | Default |
| --- | --- |
| `metadata.name` | `mlflow-iris-update-autodeploy`; choose an unused workflow name |
| Parameter `deployment-name` | `iris-classifier-updated`; choose an unused deployment name |
| Optional annotation `triton-control.ai/mlflow-s3-profile-name` | Only needed to select a profile when multiple profiles are linked |
| `s3-script-key` | Uploaded training script's full S3 key |
| `triton-image` | `nvcr.io/nvidia/tritonserver:26.06-py3` |

With exactly one of your S3 profiles linked under **Workflows → Configure S3
Secrets** in the workflow namespace, no profile annotation is needed. Triton
Control selects it automatically. The **MLflow artifact store** marker does not
override this selection.

**With multiple linked profiles, you must select one by name.** Add the annotation
to the existing `metadata` block in `workflow.yaml`. Replace `dev` with the name
of the profile marked **MLflow artifact store**:

```yaml
metadata:
  name: mlflow-iris-update-autodeploy
  annotations:
    triton-control.ai/mlflow-s3-profile-name: dev
```

Open **Triton Control → Workflows → Submit New Workflow → Edit using full workflow options**.
Paste the entire edited `workflow.yaml`, including `spec.arguments.parameters`,
then create the workflow.

The workflow has one **train** node that installs the published plugin wheels from PyPI and
training dependencies, then runs `train.py`. The script logs the update first and
only then deploys the returned model URI. Deployment happens inside that
script, not in a separate Argo step.

## Verify

Wait for **train → Succeeded**. The logs print the initial and updated model URI,
the Triton version, example predictions, and the ready deployment. If deployment
fails, the script and workflow fail.

In **MLflow**, select **Model training**, open the **triton-iris-autodeploy**
experiment, then your run and its logged model **classifier**. The model may open
in a new browser tab. On its **Artifacts** tab, expand `model_repository/classifier`:

```text
model_repository/classifier/
  config.pbtxt
  request.json
  validation.json
  1/requirements.txt
  1/model.py
  1/artifact/
  2/requirements.txt
  2/model.py
  2/artifact/
```

Open **Triton Instances → your deployment → Details → Models → classifier
(Version 2) → Infer**. Use the name selected in the `deployment-name` parameter
(default: `iris-classifier-updated`). Paste the generated `request.json` from MLflow and
click **Send / Infer**. It contains the four named Iris input tensors and the
correct output name. The unmodified example request returns `[0, 2]`.

Every execution uses the new URI returned by its first logging call. There is no
`previous-model-uri` workflow parameter and no `PREVIOUS_MODEL_URI` environment
setting. Existing models from earlier tests are not changed.

## Run the same script in code-server

When creating the **Development** workspace, leave **Install MLflow** enabled.
The workspace supplies the MLflow and Triton Control connection settings. Install
the pinned plugin versions from PyPI so it uses the same versions as the workflow.

In the code-server terminal, install both plugins from PyPI and this example's training dependencies:

```bash
cd /workspace/mlflow-iris-update-autodeploy
python3 -m pip install --user --index-url https://pypi.org/simple \
  --only-binary=mlflow-triton,mlflow-triton-control \
  "mlflow-triton==0.4.5" \
  "mlflow-triton-control==0.4.5" \
  "mlflow==3.14.0" "numpy==2.2.6" "pandas==2.3.3" \
  "scikit-learn==1.7.2" "cloudpickle==3.1.2"
```

Use the name of the profile marked **MLflow artifact store** and a new deployment
name (replace `dev` below):

```bash
export TRITON_DEPLOYMENT_NAME="iris-classifier-updated-manual"
export TRITON_CONTROL_S3_PROFILE_NAME="dev"
python3 train.py
```

The serving image defaults to `nvcr.io/nvidia/tritonserver:26.06-py3`; override it
with `TRITON_SERVING_IMAGE`. The deployment readiness timeout is 1800 seconds.
Use a new deployment name for each run; the plugin creates deployments rather
than updating existing ones.

## S3 profile mismatch

If deployment reports `model_uri must name a model in the selected S3 profile bucket`,
compare the model's `s3://<bucket>/...` artifact path with the selected profile's
bucket. They must match. The profile must also point to the storage endpoint
containing those artifacts and have read access.

The **MLflow artifact store** marker identifies the current MLflow configuration.
Older experiments can still use a previous artifact location; changing the selected
profile does not move their artifacts.
