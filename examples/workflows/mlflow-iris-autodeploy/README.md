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

Link the S3 profile under **Workflows -> Configure S3 Secrets**, as described
by the existing `sklearn-iris-training` workflow example. Upload the training
script to the bucket in that profile, using the exact object key configured in
the workflow:

| Local file | S3 object key |
| --- | --- |
| `examples/workflows/mlflow-iris-autodeploy/train.py` | `workflows/mlflow-iris-autodeploy/train.py` |

Use the bundled **code-server S3 plugin** in Explorer or **AWS CLI** in the
workspace terminal, as described in the
[sklearn Iris example](../sklearn-iris-training/README.md#3-upload-the-training-script).
For example, from the repository root in a
workspace with a configured AWS CLI profile:

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

| Location | Required value |
| --- | --- |
| `metadata.name` | A fixed, unused Kubernetes workflow name |
| `metadata.annotations/...s3-profile-name` | Name of your S3 profile |
| `s3-script-key` | Full bucket key of the uploaded `train.py` |
| `repository-prefix` | Parent directory for Triton models in that bucket |
| `triton-image` | Triton server image used for the deployment |

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
s3://<bucket>/<repository-prefix>/iris_classifier/
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
  -m s3://<bucket>/<repository-prefix>/iris_classifier \
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
open the `triton-autodeploy` experiment and verify that
`iris-classifier-sklearn` has a `best-checkpoint` alias and a
`validation_accuracy` tag. The deployed version of `iris-classifier-triton`
must have `candidate` and `champion` aliases plus the
`deployment_status=deployed` tag. In Triton Control, open the new
`iris-classifier` instance.

With a local user token and the plugin installed, the deployment can also be
queried from a terminal:

```bash
export TRITON_CONTROL_TOKEN=<local-user-token>

mlflow deployments get \
  -t triton-control://localhost:8000 \
  --name iris-classifier
```

Send a Triton HTTP inference request to the instance endpoint:

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
