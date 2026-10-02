# mlflow-triton-control

MLflow deployment target for models that already exist in a Triton S3 model
repository. Install this Python package in the environment running the MLflow
CLI or Python API. Triton Control creates the Kubernetes deployment and resolves
the selected user-owned S3 profile.

Requires Python 3.10+ and MLflow `>=3.14,<4`. Automatic CI tests the version
pinned in `charts/triton-control/values.yaml`.

```bash
python -m build
python -m pip install dist/mlflow_triton_control-*.whl
mlflow deployments help -t triton-control
```

From this directory, run the tests after installing the wheel:

```bash
python -m pip check
python -m unittest discover -s tests -v
```

The smoke tests verify installed plugin discovery, deployment requests with a
mock HTTP transport, and real model logging, registration, and retrieval with
a temporary SQLite tracking database.

## Smoke tests

Run from the repository root; each command uses a fresh temporary environment:

```bash
# Test a specific MLflow version
bash plugins/mlflow-triton-control/smoke-latest.sh 3.14.0

# Test the latest supported MLflow version
bash plugins/mlflow-triton-control/smoke-latest.sh
```

Replace `3.14.0` with the version to test. No live Kubernetes cluster is needed.
Manual CI runs accept an additional version through `mlflow_version`.

## How MLflow finds the plugin

`mlflow deployments create` is a command provided by MLflow. This package
registers the deployment target through its Python package metadata:

```toml
[project.entry-points."mlflow.deployments"]
triton-control = "mlflow_triton_control.deployment_client"
```

For `--target triton-control://triton-control:8000`, MLflow looks up the
`triton-control` entry point among installed packages, loads this module, and
calls `TritonControlDeploymentClient.create_deployment()`. That class inherits
from MLflow's `BaseDeploymentClient` and sends the deployment request to the
Triton Control API. MLflow reads entry-point metadata; it does not search
through every package's source files. The package must be installed in the
same Python environment as the `mlflow` CLI.

The short hostname works when the client runs in the Service's namespace.
For another namespace, use `triton-control://<service>.<namespace>.svc.cluster.local:8000`,
for example `triton-control://triton-control.blabla.svc.cluster.local:8000`.
Use the actual Service name created by your Helm release.

## Log a Triton model in MLflow

Like NVIDIA's Triton MLflow plugin, this package provides a `triton` model
flavor for a complete Triton model directory. The directory must contain at
least one numbered version; `config.pbtxt` may be included at
its root. The MLflow Model contains an `MLmodel` file with a `triton` flavor
entry and a copy of the entire directory under `model/`.

```python
from mlflow_triton_control import triton

model_info = triton.log_model(
    triton_model_path="/tmp/model-repository/iris_classifier",
    name="triton-model",
    registered_model_name="iris-serving",
)
print(model_info.registered_model_version)
```

`triton.load_model("models:/iris-serving/1")` downloads the registered model
and returns the local Triton model directory. Omit `registered_model_name` to
save a model without creating a Registry version. This API is in the plugin
package, not in MLflow's `mlflow.triton` namespace.

The current deployment client still accepts an already-published `s3://` Triton
repository URI. Deploying directly from `models:/...` and copying that version
to the selected S3 profile are separate planned changes. The current Iris
workflow therefore continues to use the S3 URI.

## Argo Workflows

Submit the workflow through Triton Control's authenticated Argo proxy. Mark the
deployment template with these Workflow annotations:

```yaml
metadata:
  name: train-iris
  annotations:
    triton-control.ai/mlflow-deploy-template: deploy
    triton-control.ai/mlflow-deployment-name: iris-classifier
    triton-control.ai/mlflow-s3-profile-name: workflow-training
```

The backend resolves the profile name and its linked Argo artifact repository
for the submitting user. It sets `spec.artifactRepositoryRef` and injects
`TRITON_CONTROL_TOKEN`, `TRITON_CONTROL_S3_PROFILE_ID`, and
`TRITON_CONTROL_S3_BUCKET` into the named `script` or `container` template.
Use the injected ID with `-C s3_profile_id="$TRITON_CONTROL_S3_PROFILE_ID"`.
The token is valid for 60 minutes and limited
to the specified deployment and S3 profile. The Workflow receives a five-minute
completion TTL unless it already defines one, and the Secret receives a
Workflow owner reference for garbage collection.

The opted-in Workflow must have a fixed `metadata.name` and run in the Triton
Control namespace. Direct submissions to Argo bypass this token injection.
