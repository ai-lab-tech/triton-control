# Train and Deploy Iris with MLflow

Train an Iris classifier, register it in MLflow, and deploy its ONNX model to Triton Control.

## 1. Prepare

- Enable **MLflow** and **Argo Workflows** in Triton Control. Install MLflow with an S3 artifact profile.
- Save an S3 profile under **Account → S3 Profiles** with read/write access to your bucket.
- Link it under **Workflows → Configure S3 Secrets**. The workflow profile must also reach the MLflow artifact endpoint and bucket, with read permission for the logged models.
- Allow workflow pods to download Python packages. The workflow installs `mlflow-triton-control==0.3.0`; publish that version to the package index used by the pods before running it.

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
| Annotation `triton-control.ai/mlflow-deployment-name`: deployment name | `iris-classifier` |
| Parameter `s3-script-key`: uploaded script's full object key | `workflows/mlflow-iris-autodeploy/train.py` |
| Parameter `triton-image`: serving image | `nvcr.io/nvidia/tritonserver:26.06-py3` |

MLflow stores both logged models under its configured S3 artifact root. The training
step returns the concrete Triton repository URI as an Argo output parameter; the
deploy step uses that URI directly. Its path is independent of the script key.
You only upload `train.py`; no separate model upload or repository-path input is needed.

The deploy command reads its name from the deployment annotation. Keep the model name `iris_classifier`.

## 4. Run

1. Open **Workflows** in Triton Control.
2. In the embedded Argo UI, click **Submit New Workflow**.
3. Select **Edit using full workflow options** and paste the complete edited [workflow.yaml](workflow.yaml), or select it with **Upload File**.
4. Check your S3 profile name, script key, workflow name, and deployment name.
5. Click **Submit**, then open the created workflow.
6. Click the **train** node and open its logs to follow training.
7. After **train** succeeds, **deploy** starts automatically. Open its logs to follow deployment.
8. Wait until both nodes show **Succeeded**, then continue to verification below.

Triton Control injects credentials automatically and records your user as the MLflow run creator.
You do not need to copy a login token or set a creator tag.

For another run, choose unused workflow and deployment names, or delete the previous ones first.
The deployment plugin creates new deployments; it does not update existing ones.

## 5. Verify

In **MLflow**:

- Open experiment **triton-autodeploy** to see the run and accuracy.
- Open **Model Registry**:

| Registered model | Purpose | Check |
| --- | --- | --- |
| `iris-classifier-sklearn` | Original Python checkpoint; kept for evaluation or later training | `best-checkpoint` alias and `validation_accuracy` tag |
| `iris-classifier-triton` | Exported ONNX repository served by Triton | Deployed version has `candidate`, `champion`, and `deployment_status=deployed` |

Both models are saved through the MLflow proxy to its S3 artifact store.
Argo does not make a separate serving-repository copy.
Each run creates new versions; `best-checkpoint` changes only when accuracy improves.
The registered Triton model version has a `triton_model_uri` tag containing the
concrete S3 repository URI. Copy that value for manual deployment. Artifacts are
uploaded when `log_model()` runs; registration records a version referencing them.

A later training run can load the selected sklearn checkpoint directly:

```python
model = mlflow.sklearn.load_model("models:/iris-classifier-sklearn@best-checkpoint")
```

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


## 6. Manual CLI Verification

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
python3 -m pip install --user "mlflow-triton-control==0.3.0"
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
