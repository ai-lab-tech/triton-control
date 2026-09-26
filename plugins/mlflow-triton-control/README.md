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

See [the requirements](mlflow-triton-plugin-requirements.md) for the target URI,
authentication, API extensions and current feature boundaries.

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

The backend resolves the profile name for the submitting user. It creates
a temporary token Secret and injects `TRITON_CONTROL_TOKEN` and
`TRITON_CONTROL_S3_PROFILE_ID` into the named `script` or `container` template.
Use the injected ID with `-C s3_profile_id="$TRITON_CONTROL_S3_PROFILE_ID"`.
The token is valid for 60 minutes and limited
to the specified deployment and S3 profile. The Workflow receives a five-minute
completion TTL unless it already defines one, and the Secret receives a
Workflow owner reference for garbage collection.

The opted-in Workflow must have a fixed `metadata.name` and run in the Triton
Control namespace. Direct submissions to Argo bypass this token injection.
