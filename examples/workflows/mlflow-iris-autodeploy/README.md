# Train and Deploy Iris with MLflow

Train an Iris classifier, register it in MLflow, and deploy its ONNX model to Triton Control.

## 1. Prepare

- Enable **MLflow** and **Argo Workflows** in Triton Control.
- Save an S3 profile under **Account → S3 Profiles** with read/write access to your bucket.
- Link it under **Workflows → Configure S3 Secrets**.
- Allow workflow pods to download Python packages. The workflow installs the MLflow plugin automatically.

## 2. Upload `train.py` with Code-Server

1. Open **Development** and create a workspace:
   - Image: `nvcr.io/nvidia/tritonserver:26.06-py3`
   - **Image already has Development installed**: disabled
   - Storage: at least `5Gi`; GPUs: `0`
2. Wait for readiness and open code-server.
3. Copy [train.py](train.py) and [workflow.yaml](workflow.yaml) into `/workspace/mlflow-iris-autodeploy/`.
4. In **Explorer**, right-click the folder → **S3 Operations → Choose Profile…** and select your linked S3 profile.
5. Under **S3 · <profile> · <bucket>**, create `workflows/mlflow-iris-autodeploy/`.
6. Drag the local `train.py` into that S3 folder and wait for the upload to finish.

If your profile has a prefix such as `team-a`, include it in the full object key:
`team-a/workflows/mlflow-iris-autodeploy/train.py`.

## 3. Configure `workflow.yaml`

| Value | Example |
| --- | --- |
| `metadata.name`: unused workflow name | `mlflow-iris-autodeploy` |
| Annotation `triton-control.ai/mlflow-s3-profile-name`: your profile name | `workflow-training` |
| Parameter `s3-script-key`: uploaded script's full object key | `workflows/mlflow-iris-autodeploy/train.py` |
| Parameter `triton-image`: serving image | `nvcr.io/nvidia/tritonserver:26.06-py3` |

The model is uploaded automatically beside the script:
`workflows/mlflow-iris-autodeploy/iris_classifier/`.
You only upload `train.py`; no separate model upload or repository-path input is needed.

Keep the default deployment name `iris-classifier` and model name `iris_classifier`.
If renaming the deployment, update both its annotation and `--name` in the deploy command.

## 4. Run

Open **Workflows** in Triton Control and submit the edited YAML through the embedded Argo UI.
Wait for both **train** and **deploy** to succeed.

Triton Control injects credentials automatically and records your user as the MLflow run creator.
You do not need to copy a login token or set a creator tag.

For another run, delete the previous workflow and the `iris-classifier` deployment first.
The deployment plugin creates new deployments; it does not update existing ones.

## 5. Verify

In **MLflow**:

- Open experiment **triton-autodeploy** to see the run and accuracy.
- Open **Model Registry**:

| Registered model | Purpose | Check |
| --- | --- | --- |
| `iris-classifier-sklearn` | Original Python checkpoint; kept for evaluation or later training | `best-checkpoint` alias and `validation_accuracy` tag |
| `iris-classifier-triton` | Exported ONNX repository served by Triton | Deployed version has `candidate`, `champion`, and `deployment_status=deployed` |

Both models are saved as MLflow artifacts. Argo also uploads the serving repository to S3 for deployment.
Each run creates new versions; `best-checkpoint` changes only when accuracy improves.

In **Triton Control**:

1. Open **Triton Instances → iris-classifier** and check readiness.
2. Open **Models → iris_classifier**, version **1**, then click **Infer**.
3. Replace **JSON Body** with:

```json
{
  "inputs": [{
    "name": "input",
    "shape": [1, 4],
    "datatype": "FP32",
    "data": [5.1, 3.5, 1.4, 0.2]
  }],
  "outputs": [{"name": "label"}, {"name": "probabilities"}]
}
```

4. Click **Send / Infer**. The response contains a label and class probabilities.
