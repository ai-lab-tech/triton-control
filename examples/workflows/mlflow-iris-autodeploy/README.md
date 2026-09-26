# MLflow Iris Training with Automatic Triton Deployment

This workflow trains a scikit-learn Iris classifier, records the run and model
in MLflow, exports the classifier as an ONNX Triton model repository, uploads
that repository to S3, and deploys it automatically through the
`mlflow-triton-control` plugin.

```text
train -> MLflow tracking and registry
      -> ONNX Triton repository -> S3
                                  -> mlflow deployments create
                                  -> Triton Control -> Triton
```

The plugin does not convert `models:/...` artifacts in its current version.
The training step therefore performs the ONNX export and creates
`config.pbtxt`; the deployment step passes the resulting S3 model URI to the
plugin.

## Prerequisites

- MLflow and Argo Workflows are enabled in Triton Control.
- An S3 profile is linked under **Workflows -> Configure S3 Secrets**.
- The same profile can write the repository prefix and can be used for Triton
  deployments.
- The selected S3 bucket and Triton Control service are reachable from the
  workflow namespace.
- The workflow pods can clone this repository from GitHub over HTTPS.
- The workflow pods can download Python packages from the configured package
  index.

## 1. Push the Source Branch

Push the example and plugin to the Git revision configured in `workflow.yaml`:

```bash
git push origin feature/mlflow-triton-control-implementation
```

Argo clones this branch directly from GitHub for both workflow steps. The
training step runs `train.py` from the checkout, and the deployment step
installs `plugins/mlflow-triton-control` from the same checkout. No Wheel build
or upload is required.

## 2. Configure S3 Access

Link the S3 profile under **Workflows -> Configure S3 Secrets**, as described
by the existing `sklearn-iris-training` workflow example. S3 stores only the
generated Triton model repository. No source files or plugin packages need to
be uploaded. Both steps use `python:3.12-slim` and install their dependencies
at runtime. The `triton-image` parameter remains the separate NVIDIA Triton
image that serves the exported model.

## 3. Configure the Workflow

Edit [workflow.yaml](workflow.yaml) before submitting it:

| Location | Required value |
| --- | --- |
| `metadata.name` | A fixed, unused Kubernetes workflow name |
| `metadata.annotations/...s3-profile-id` | ID of your S3 profile |
| `artifactRepositoryRef.configMap` | ConfigMap shown by **Configure S3 Secrets** |
| `git-repository` | HTTPS clone URL of this GitHub repository |
| `git-revision` | Pushed branch, tag, or commit containing the example and plugin |
| `mlflow-tracking-uri` | Internal MLflow service URL |
| `triton-control-target` | `triton-control://` URI for the backend service and API port |
| `s3-bucket` | Bucket from the selected S3 profile |
| `repository-prefix` | Parent directory for Triton models in that bucket |
| `s3-profile-id` | Same profile ID as the annotation |
| `triton-image` | Triton server image used for the deployment |

The MLflow deployment name is `iris-classifier` in the annotation and the
deployment command. The Triton model name is `iris_classifier` in `train.py`,
the S3 model URI, and the artifact key. Keep each name consistent in those
locations if you rename it. The bucket must match the selected S3 profile.

The configured Git revision must already exist on GitHub when the workflow is
submitted. For reproducible runs, replace the branch name with a commit SHA.

The fixed workflow name is required by the current token delegation. Delete a
completed workflow before submitting it again, or change `metadata.name` for
the next run.

## 4. Submit through Triton Control

Open **Workflows** in Triton Control and submit the edited manifest through its
Argo UI. The request must pass through Triton Control's authenticated Argo
proxy. A direct request to Argo Server bypasses deployment-token injection.

For an opted-in workflow, Triton Control:

1. verifies that the current user owns the annotated S3 profile;
2. creates a short-lived token limited to `iris-classifier` and that profile;
3. injects `TRITON_CONTROL_TOKEN` into the `deploy` template through a
   temporary Kubernetes Secret;
4. attaches the Secret to the Workflow for garbage collection.

The `train` task records parameters, accuracy, tags, an MLflow sklearn model,
and a registered model version. It exports this repository:

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
  -C s3_profile_id=<profile-id> \
  -C image=nvcr.io/nvidia/tritonserver:26.06-py3
```

The command finishes only after the Triton server and the concrete
`iris_classifier` model report readiness.

## 5. Verify the Deployment

The workflow should contain successful `train` and `deploy` nodes. In MLflow,
open the `triton-autodeploy` experiment and the registered
`iris-classifier` model. In Triton Control, open the new `iris-classifier`
instance.

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
