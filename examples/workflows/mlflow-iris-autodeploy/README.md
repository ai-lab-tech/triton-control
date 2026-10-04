# MLflow Iris Training with Automatic Triton Deployment

This workflow trains a scikit-learn Iris classifier, logs the training
checkpoint in MLflow, exports an ONNX Triton repository, registers that
repository in the MLflow Model Registry with the plugin's Triton flavor, and
uploads a serving copy to S3 for deployment through Triton Control.

```text
train -> MLflow run + sklearn checkpoint -> MLflow Registry + best-checkpoint alias
      -> Triton repository -> MLflow Registry (triton flavor) + candidate alias
                         `-> S3 -> Triton Control -> Triton -> champion alias
```

## MLflow Artifacts and Registry

The training step logs the sklearn checkpoint as an MLflow Logged Model named
`sklearn-checkpoint` and registers it as `iris-classifier-sklearn`. It then
packages the complete Triton repository with
`mlflow_triton_control.triton.log_model()` and registers that model as
`iris-classifier-triton`. Each workflow run creates a new version of both
registered models. The sklearn version receives a `validation_accuracy` tag and
becomes `best-checkpoint` only when its accuracy is higher than the previous
best version. The new Triton version starts as `candidate`. The Triton
Registry version has the `triton` flavor and contains the repository files,
including `config.pbtxt` and the numbered ONNX model.

### Why the Registry Contains Two Models

- **`iris-classifier-sklearn`** is the original trained scikit-learn pipeline.
  Keep it for loading in Python, evaluation, or later training. Its
  `best-checkpoint` alias identifies the version with the highest validation
  accuracy.
- **`iris-classifier-triton`** is the exported ONNX model packaged as a Triton
  repository. Its `champion` alias identifies the successfully deployed
  version. This is the model served by Triton Control.

The aliases can point to different version numbers. For example, after three
workflow runs, `best-checkpoint` may still point to sklearn Version 1 if neither
later run improved its accuracy, while `champion` points to Triton Version 3
after that version was successfully deployed. The sklearn checkpoint is not
deployed to Triton Control.

A later training run can load the selected sklearn checkpoint directly:

```python
model = mlflow.sklearn.load_model(
    "models:/iris-classifier-sklearn@best-checkpoint"
)
```

Argo also uploads the same Triton repository to S3. The current deployment
client accepts an S3 model URI, so the deploy step serves that S3 copy. After
the deployment client reports the instance and model as ready, the deploy step
sets `deployment_status=deployed` and moves the Triton model's `champion` alias
to that version. Direct deployment from `models:/...` and an inference-based
smoke test are not part of this workflow yet.

## Prerequisites

- MLflow and Argo Workflows are enabled in Triton Control.
- An S3 profile is linked under **Workflows -> Configure S3 Secrets**.
- The same profile can write the repository prefix and can be used for Triton
  deployments.
- The selected S3 bucket and Triton Control service are reachable from the
  workflow namespace.
- The workflow pods can download Python packages from the configured package
  index.

## 1. Plugin Package

Both the train and deploy steps install the published
[`mlflow-triton-control` 0.2.0 package from PyPI](https://pypi.org/project/mlflow-triton-control/0.2.0/)
with `pip`. No plugin wheel needs to be uploaded to S3. When you publish a new
plugin release, update the pinned package version in [workflow.yaml](workflow.yaml).

## 2. Upload the Training Code and Configure S3 Access

Save your bucket's S3 profile under the account menu's **S3 Profiles**, then
link the same profile under **Workflows -> Configure S3 Secrets**. Upload the
training script to that bucket using the exact object key configured in the
workflow:

| Local file | S3 object key |
| --- | --- |
| `examples/workflows/mlflow-iris-autodeploy/train.py` | `workflows/mlflow-iris-autodeploy/train.py` |

### Start Code-Server and Upload with the S3 Plugin

1. Open **Development** in Triton Control and create a CPU-only workspace:
   use `nvcr.io/nvidia/tritonserver:26.06-py3`, disable **Image already has
   Development installed**, set workspace storage to at least `5Gi`, and set
   GPU count to `0`.
2. Wait until the workspace is ready, then open code-server from
   **Development**. Triton Control installs code-server and the bundled
   **Triton Control Deploy** plugin with its S3 browser in **Explorer**.
3. Create `/workspace/mlflow-iris-autodeploy` and copy or upload this example's
   `train.py` and `workflow.yaml` into it.
4. In **Explorer**, right-click the workspace folder and choose
   **S3 Operations → Choose Profile…**. Select the same S3 profile linked under
   **Workflows -> Configure S3 Secrets**. If already connected, use
   **S3 Operations → Switch Profile…**.
5. Expand **S3 · <profile name> · <bucket>** and create or open
   `workflows/mlflow-iris-autodeploy/` under that S3 root.
6. Drag the local `train.py` from the workspace tree onto that S3 folder.
   This copies the file and keeps the local source. Wait for the transfer to
   finish, then open `train.py` from the S3 tree to verify the upload.

You can also use **S3 Operations → Copy** on the workspace file and
**S3 Operations → Paste** on the destination S3 folder.

If the S3 profile has a browsing prefix such as `team-a`, the S3 root starts at
that prefix. The full uploaded object key is then
`team-a/workflows/mlflow-iris-autodeploy/train.py`; set `s3-script-key` in
`workflow.yaml` to that full key.

### Alternative: AWS CLI

To upload from the workspace terminal, follow the
[AWS CLI setup instructions](../sklearn-iris-training/README.md#aws-cli).
AWS CLI profiles are configured separately from Triton Control S3 profiles.
From the repository root in a workspace with a configured AWS CLI profile:

```bash
aws --profile workflow-training --endpoint-url https://<your-s3-endpoint> \
  s3 cp examples/workflows/mlflow-iris-autodeploy/train.py \
  s3://<profile-bucket>/workflows/mlflow-iris-autodeploy/train.py
```

The bucket and endpoint come from your S3 profile; the workflow contains only
the script's object key. Both steps use `python:3.12-slim` and install their Python
dependencies at runtime. The `triton-image` parameter selects the separate
NVIDIA Triton image that serves the exported model.

## 3. Configure the Workflow

Edit [workflow.yaml](workflow.yaml) before submitting it:

| Location | Required value | Example |
| --- | --- | --- |
| `metadata.name` | A fixed, unused Kubernetes workflow name | `mlflow-iris-autodeploy` |
| `metadata.annotations/...s3-profile-name` | Name of your S3 profile | `workflow-training` (replace with your saved profile name) |
| `s3-script-key` | Full bucket key of the uploaded `train.py`, including any profile prefix | `workflows/mlflow-iris-autodeploy/train.py` or `team-a/workflows/mlflow-iris-autodeploy/train.py` |
| `triton-image` | Triton server image used for the deployment | `nvcr.io/nvidia/tritonserver:26.06-py3` |

The workflow derives the serving model's S3 path from the parent directory of
`s3-script-key`; no separate repository prefix is needed. For example,
`workflows/mlflow-iris-autodeploy/train.py` produces
`workflows/mlflow-iris-autodeploy/iris_classifier/`. A profile prefix included in
`s3-script-key` is preserved. If the script is at the bucket root (`train.py`),
the model is uploaded to `iris_classifier/` at the bucket root.

Triton Control resolves the selected profile's linked Argo artifact-repository
ConfigMap and bucket when the authenticated proxy receives the workflow. No
ConfigMap name or bucket parameter is needed in this manifest.
The training template uses the cluster's fixed MLflow service URL,
`http://mlflow-service:5000`; edit `MLFLOW_TRACKING_URI` in the template if
your installation uses a different service address.

The MLflow deployment name is `iris-classifier` in the annotation and the
deployment command. The Triton model name is `iris_classifier` in `train.py`,
the S3 model URI, and the artifact key. Keep each name consistent in those
locations if you rename it. The bucket must match the selected S3 profile.

For reproducible runs, use a unique S3 key for each training-script version
instead of overwriting a file used by existing workflows. The plugin version
is pinned in the `deploy` command.

The fixed workflow name is required by the current token delegation. Delete a
completed workflow before submitting it again, or change `metadata.name` for
the next run.

## 4. Submit through Triton Control

Open **Workflows** in Triton Control and submit the edited manifest through its
Argo UI. The request must pass through Triton Control's authenticated Argo
proxy. A direct request to Argo Server bypasses deployment-token injection.

For an opted-in workflow, Triton Control:

1. resolves the annotated S3 profile name and its linked Argo artifact
   repository for the current user;
2. creates a short-lived token limited to `iris-classifier` and that profile;
3. injects `TRITON_CONTROL_TOKEN` through a temporary Kubernetes Secret and
   injects `TRITON_CONTROL_S3_PROFILE_ID` and `TRITON_CONTROL_S3_BUCKET` into
   the `deploy` template;
4. attaches the Secret to the Workflow for garbage collection.

The `train` task records parameters, accuracy, and tags; registers the sklearn
checkpoint as a new version of `iris-classifier-sklearn`; and registers the
Triton repository as a new version of `iris-classifier-triton`. It updates
`best-checkpoint` when the new sklearn version has the best accuracy and assigns
`candidate` to the new Triton version. Argo then uploads this repository:

```text
s3://<bucket>/<script-directory>/iris_classifier/
|-- config.pbtxt
|-- training-metadata.json
`-- 1/
    `-- model.onnx
```

After Argo finishes uploading that output artifact, the dependent `deploy`
task runs:

```bash
mlflow deployments create \
  -t triton-control://<triton-control-service>:8000 \
  --name iris-classifier \
  -m s3://<bucket>/<script-directory>/iris_classifier \
  -C s3_profile_id="$TRITON_CONTROL_S3_PROFILE_ID" \
  -C image=nvcr.io/nvidia/tritonserver:26.06-py3
```

Use the actual Triton Control Service name. For clients in another namespace,
use `triton-control://<service>.<namespace>.svc.cluster.local:8000`.

The command finishes only after the Triton server and the concrete
`iris_classifier` model report readiness. The deploy step then tags the exact
Triton Registry version passed from the train step as deployed and assigns its
`champion` alias. The `candidate` alias remains available on that version.
`mlflow deployments create` returns HTTP 409 if a deployment named
`iris-classifier` already exists; this plugin version does not support updating
deployments. To run the example again, delete the existing deployment first or
choose a new deployment name in both the Workflow annotation and command.

## 5. Verify the Deployment

The workflow should contain successful `train` and `deploy` nodes. In MLflow,
open the `triton-autodeploy` experiment to inspect the training run and accuracy.
Then open **Model Registry** from the MLflow menu:

- Select **iris-classifier-sklearn** and verify that the best model version has
  the `best-checkpoint` alias and a `validation_accuracy` tag.
- Select **iris-classifier-triton** and open the deployed model version. Verify
  that it has the `candidate` and `champion` aliases plus the
  `deployment_status=deployed` tag.

### Verify and Test in Triton Control

1. Open **Triton Instances** and select **iris-classifier** to check that the
   deployed server is live and ready.
2. Open its **Models** tab and check that **iris_classifier**, version **1**,
   is ready, then click **Infer** on that model.
3. In the **Model Inference** page, replace the **JSON Body** with:

   ```json
   {
     "inputs": [{
       "name": "input",
       "shape": [1, 4],
       "datatype": "FP32",
       "data": [5.1, 3.5, 1.4, 0.2]
     }],
     "outputs": [
       {"name": "label"},
       {"name": "probabilities"}
     ]
   }
   ```

4. Click **Send / Infer** and inspect the **Response** for the predicted
   label and class probabilities.


### Alternative: Verify from the Internal Code-Server Terminal

Open your workspace's code-server from **Development**, then open its integrated
terminal. Run these commands inside that workspace using the cluster-internal
Triton Control Service address. To get your local user token:

1. Sign in to Triton Control with your local email and password.
2. In that Triton Control browser tab, open the browser's developer tools and
   select **Application → Local Storage** (or **Storage → Local Storage** in
   Firefox).
3. Select the Triton Control site and copy the value of `triton_access_token`.
4. Paste it inside the quotes in the code-server terminal command below.

If that storage entry is missing, use the **Network** tab in developer tools:
sign in again with your local email and password, select the `POST /api/auth/login`
request, and copy `access_token` from its JSON response. Inspect the Triton
Control browser tab, rather than the embedded MLflow or code-server frame.
An existing browser session can authenticate through its cookie without a
token in Local Storage. OIDC/SSO login also uses a browser session and does not
provide this local-login token; the instructions above require a local account.

This is your login access token; if it expires, sign in again and copy the new
value. The workflow's temporary deployment token is injected automatically and
does not need to be copied for workflow execution.

```bash
python3 -m pip install --user "mlflow-triton-control==0.2.0"
export PATH="$HOME/.local/bin:$PATH"
export TRITON_CONTROL_TOKEN="<paste-your-login-access-token>"

mlflow deployments get \
  -t triton-control://triton-control.triton-control.svc.cluster.local:8000 \
  --name iris-classifier
```

From the same code-server terminal, send an inference request to the Triton
instance's internal HTTP endpoint. Use the instance URL shown in Triton Control
for `<triton-endpoint>` (host and port), adjusting the Service name or namespace
if your installation uses different values:

```bash
curl -sS http://<triton-endpoint>/v2/models/iris_classifier/infer \
  -H 'Content-Type: application/json' \
  -d '{
    "inputs": [{
      "name": "input",
      "shape": [1, 4],
      "datatype": "FP32",
      "data": [5.1, 3.5, 1.4, 0.2]
    }],
    "outputs": [
      {"name": "label"},
      {"name": "probabilities"}
    ]
  }'
```

The exact output names are also written to `training-metadata.json` in the S3
model directory.
