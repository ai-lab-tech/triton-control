# mlflow-triton-control

MLflow deployment target for models that already exist in a Triton S3 model
repository. Install this Python package in the environment running the MLflow
CLI or Python API. Triton Control creates the Kubernetes deployment and resolves
the selected user-owned S3 profile.

```bash
python -m build
python -m pip install dist/mlflow_triton_control-*.whl
mlflow deployments help -t triton-control
```

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
