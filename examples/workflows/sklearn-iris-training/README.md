# scikit-learn Iris Training Workflow

Train a small scikit-learn Iris classifier with Argo Workflows and store the
training script, trained model, and evaluation results in S3-compatible object
storage. The workflow uses ephemeral container storage and does not mount a
code-server workspace PVC.

## Files

| File | Use |
| --- | --- |
| `train_iris.py` | Training script uploaded to S3 and downloaded by Argo |
| `workflow.yaml` | Argo Workflow with native S3 input and output artifacts |
| `screenshots/` | UI screenshots for workspace and Argo Workflow submission |

## Prerequisites

- Argo Workflows is enabled in Triton Control.
- An S3-compatible bucket already exists.
- The S3 credentials can read the training-script object and write below the
  selected output prefix.
- `train_iris.py` is available in a development workspace or another machine
  with an S3 client.

## 1. Configure the Workflow S3 Secret

In Triton Control, open **Workflows**, select **Configure S3 Secrets**, and add
the Access Key ID and Secret Access Key for the bucket. Triton Control creates
an opaque Kubernetes Secret in the Argo Workflow namespace.

Copy the generated **Secret** name shown in the credentials dialog. Only this
name is placed in `workflow.yaml`; never put either credential value in the
manifest.

The generated Secret contains the keys expected by this example:

```text
access-key-id
secret-access-key
```

## 2. Upload the Training Script

Open code-server from **Development** and upload `train_iris.py` with your
configured S3 client. For example, with the AWS CLI and credentials supplied by
the client environment or profile:

```bash
aws --endpoint-url https://s3.example.com \
  s3 cp train_iris.py \
  s3://triton-artifacts/workflows/sklearn-iris-training/train_iris.py
```

The optional [S3/R2 Explorer](../../../docs/development-workspaces.md#optional-install-s3r2-explorer)
can upload the file from the code-server Explorer instead. Whichever client is
used, the object key must match the workflow's `s3-script-key` parameter.

![Create a Triton Control workspace](screenshots/create-workspace.png)

## 3. Configure the Workflow

Update the parameters under `spec.arguments.parameters` in `workflow.yaml`:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `s3-endpoint` | `s3.example.com` | S3 API host, optionally with a port; omit `https://` |
| `s3-region` | `us-east-1` | Bucket region |
| `s3-bucket` | `triton-artifacts` | Existing bucket name |
| `s3-credentials-secret` | `workflow-s3-training-a1b2c3` | Secret name displayed by Triton Control |
| `s3-script-key` | `workflows/sklearn-iris-training/train_iris.py` | Uploaded source object |
| `s3-output-prefix` | `workflows/sklearn-iris-training/runs` | Parent prefix for run outputs |

The manifest uses TLS for S3 by default. For a private endpoint signed by a
custom CA, create a Secret containing the CA certificate in the Workflow
namespace and add this selector to both `s3` blocks:

```yaml
caSecret:
  name: workflow-s3-ca
  key: ca.crt
```

For an intentionally plain-HTTP development endpoint, add `insecure: true` to
both `s3` blocks. Do not use that setting for an HTTPS endpoint.

## 4. Submit the Workflow

In Argo Workflows:

1. Select **Submit New Workflow**.
2. Select **Edit using full workflow options**.
3. Paste the configured contents of `workflow.yaml`.
4. Create the workflow.

![Submit a new workflow in Argo Workflows](screenshots/argo-submit-new-workflow.png)

![Open the full workflow editor](screenshots/argo-edit-full-workflow-options.png)

![Create the workflow from the manifest](screenshots/argo-create-workflow-manifest.png)

Before the training container starts, the Argo executor downloads
`s3-script-key` to `/tmp/train_iris.py`. The container installs the pinned
Python packages and writes all training results to `/tmp/outputs`. After the
container exits, the executor uploads that directory to S3. A missing source
object, invalid credentials, or a failed upload makes the Workflow fail.

## 5. Check the Outputs

Each run gets its own prefix derived from the generated Workflow name:

```text
s3://triton-artifacts/workflows/sklearn-iris-training/runs/
`-- sklearn-iris-training-abc12/
    |-- accuracy.txt
    |-- iris-logreg-model.joblib
    |-- labels.txt
    `-- metrics.json
```

The Argo Workflow also exposes `accuracy` as an output parameter and records
the S3 location as the `training-results` output artifact.

![Successful Argo Workflow run](screenshots/argo-workflow-succeeded.png)

## Optional Local Smoke Test

The same training script can run locally without S3 or Argo:

```bash
python -m pip install scikit-learn==1.5.2 joblib==1.4.2
python train_iris.py --output-dir /tmp/iris-training
ls -l /tmp/iris-training
```
