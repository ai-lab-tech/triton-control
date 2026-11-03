# Generate an Iris Triton Model with MLflow

Train a small sklearn pipeline and test only `mlflow_triton.sklearn.log_model()`.
Uses core package `mlflow-triton==0.4.1`. No deployment integration or custom
serving image is required.

```python
from mlflow_triton import sklearn

info = sklearn.log_model(
    fitted_pipeline,
    name="classifier",
    input_example=example_dataframe,
)
print(info.model_uri)
```

This call saves the native sklearn model and generates its Triton structure
in the MLflow artifact store. It does not launch Triton. The example does not
download a local copy, resolve S3 model URIs or create a deployment.

## Run through Argo

1. Enable MLflow with S3 artifact storage and Argo Workflows in Triton Control.
2. Link your S3 profile under **Workflows → Configure S3 Secrets**. Copy the
   displayed artifact repository ConfigMap name into
   `spec.artifactRepositoryRef.configMap` in `workflow.yaml`.
3. Upload `train.py` with code-server to S3 key
   `workflows/mlflow-iris-generate/train.py`. Include any profile prefix in the
   workflow's `s3-script-key` parameter.
4. Submit `workflow.yaml` through **Workflows** in Triton Control. The authenticated
   proxy automatically injects MLflow tracking credentials; no deployment
   annotations or deployment token are needed.
5. Open the **train** node logs and copy the printed MLflow model URI.

## Verify generation

In MLflow, open experiment **triton-iris-generate**, then the run and its logged
model **classifier**. Inspect the model's artifacts:

```text
MLmodel
... native sklearn model files ...
model_repository/classifier/
  config.pbtxt
  requirements.txt
  request.json
  validation.json
  1/
    model.py
    artifact/
```

`requirements.txt` is generated automatically. `validation.json` reports local
prediction/mapping validation; actual Triton execution is not part of this test.
No Model Registry registration is requested.

## Run directly in a workspace

Use a Python environment configured with your MLflow tracking URI and credentials:

```bash
python3 -m pip install 'mlflow==3.14.0' 'mlflow-triton==0.4.1' \
  'numpy==2.2.6' 'scikit-learn==1.7.2' 'pandas==2.3.3' 'cloudpickle==3.1.2'
python3 train.py
```

Workspaces do not receive Argo's automatic tracking credentials. Set
`MLFLOW_TRACKING_URI` and the appropriate authentication for your MLflow server
before running directly.
