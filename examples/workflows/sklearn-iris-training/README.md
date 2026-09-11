# scikit-learn Iris Training Workflow

This example follows a small data-science loop in Triton Control: develop a
training script in a Development workspace, upload it to S3-compatible object
storage, then run it as an Argo Workflow. The workflow downloads the script,
trains an Iris classifier, and stores the model and evaluation results back in
the object store.


## Prerequisites

- Argo Workflows is enabled in Triton Control.
- You have an existing S3-compatible bucket and credentials that can read the
  uploaded training script and write below the selected output prefix.


## 1. Create a Development Workspace

In Triton Control, open **Development** and create a CPU-only workspace for
writing and testing the training code:

| Field | Value |
| --- | --- |
| Triton development image | `nvcr.io/nvidia/tritonserver:26.06-py3` |
| Image already has Development installed | Disabled |
| Workspace storage | At least `5Gi` |
| GPU count | `0` |

When the workspace is ready, open code-server from **Development**. Triton
Control installs the Development runtime because the Python image does not
include code-server.

![Create a Triton Control workspace](screenshots/create-workspace-v2.png)

## 2. Create the Training Code in the Workspace

In the workspace, create a directory for the example: `/workspace/sklearn-iris-training`

Then copy or upload `train_iris.py` and `workflow.yaml` from this example into
that directory. `train_iris.py` is the training code that the workflow will execute. It trains
a `StandardScaler` plus `LogisticRegression` pipeline on
`sklearn.datasets.load_iris` and writes the training results to its output
directory.


Edit the script in the workspace to try a different model, feature processing,
or training arguments. Keep the output files, or update the validation and
artifact expectations in `workflow.yaml` to match your own script.

## 3. Install, Configure, and Use an S3 Client

The workspace needs an S3 client to upload the training script. In code-server's
Extensions view, install [More Connect](https://open-vsx.org/extension/ucodkr/more-connect)
(`ucodkr.more-connect`), or use the AWS CLI from the terminal.

In **More Connect → S3 Browser**, add an S3 host and select **AWS S3**, **MinIO**,
or **S3 Compatible**. Enter the S3 API endpoint without a bucket name, region,
access key ID, and secret access key. MinIO automatically uses path-style access;
for other compatible providers, select the addressing mode they require.
More Connect supports HTTP and HTTPS. For HTTPS, the hostname must match the
server certificate; the endpoint must be reachable from the workspace pod.

For a private/custom CA, create a ConfigMap containing the public PEM CA bundle
in the workspace namespace and configure these Helm values:

```yaml
development:
  codeServer:
    extraCaConfigMap: s3-custom-ca
    extraCaKey: ca-bundle.pem
```

Follow the [workspace HTTPS certificate setup](../../../charts/triton-control/README.md#development-workspace-https-certificates)
for the ConfigMap command and rollout requirements, including existing workspaces.
The chart sets `NODE_EXTRA_CA_CERTS` for Node.js extensions at startup; exporting
it in an already-open terminal does not update the running explorer. Endpoints
with certificates already trusted by Node need no extra CA bundle.

To upload the example folder with More Connect:

1. In your bucket, create/open the destination `workflows/sklearn-iris-training/`.
2. Click the **cloud-upload icon beside that S3 folder**, then choose **Upload Folder**.
3. Select `/workspace/sklearn-iris-training/` and click **Upload**.

More Connect uploads the folder's **contents**, including subdirectories, into
the selected S3 destination; it does not add the outer folder name. Version 0.1.31
supports **Upload Folder**, but not drag-and-drop.

Use **More Connect** for folder uploads; **S3/R2 Explorer** does not support them.

For the AWS CLI path, run:

```bash
python -m pip install --user awscli
aws configure --profile workflow-training
```

If you configured the custom CA mount above, AWS CLI needs its own
[`AWS_CA_BUNDLE`](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-envvars.html)
setting in the terminal before uploading:

```bash
export AWS_CA_BUNDLE=/etc/triton-control/code-server-ca/ca.pem
```

At the prompts, enter the access key ID, secret access key, bucket region, and
your preferred output format. For an S3-compatible provider such as Cloudflare
R2, use that provider's S3 API credentials; the endpoint is supplied when you
upload. Do not put these credential values in `workflow.yaml`.

From the example directory in the workspace, upload the script. Replace the
endpoint and bucket with your own values. In the command,
`https://<your-s3-endpoint>` is the S3 API endpoint;
`s3://<your-s3-bucket>/...` is the destination made of the bucket name and
object key.

```bash
cd /workspace/sklearn-iris-training
aws --profile workflow-training \
  --endpoint-url https://<your-s3-endpoint> \
  s3 cp train_iris.py \
  s3://<your-s3-bucket>/workflows/sklearn-iris-training/train_iris.py
```

## 4. Configure the Workflow S3 Secret

In **Workflows → Configure S3 Secrets → Add S3 Credentials**, choose an existing
S3 profile under **Credential source**, enter a name, and save. Triton Control
creates a linked Secret using the profile's credentials and CA certificate.
Manual entry and optional CA upload/paste are also available; no kubectl is needed.

Profile changes automatically update linked Secrets without changing their names.
The dialog shows the linked profile, last sync time, and any sync error; the profile
owner can use **Sync now** to retry. Remove linked workflow credentials before
deleting their source profile.

New runs use the updated credentials; running workflows may retain previously
loaded values. Endpoint, bucket, region, and object paths remain workflow parameters
and must be updated separately. If you remove the profile's CA, also remove any
`caSecret` reference that requires it from your workflow.

Copy the generated **Secret** name shown in the credentials dialog. The
workflow uses only this name; it never contains the credential values. The
generated Secret has the keys expected by this example:

```text
access-key-id
secret-access-key
```

## 5. Configure the Workflow in the Workspace

Back in code-server, open `/workspace/sklearn-iris-training/workflow.yaml` and
update the parameters under `spec.arguments.parameters`:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `s3-endpoint` | `s3.example.com` | S3 API host, optionally with a port; omit `https://` |
| `s3-region` | `us-east-1` | Bucket region |
| `s3-bucket` | `triton-artifacts` | Existing bucket name |
| `s3-credentials-secret` | `workflow-s3-training-a1b2c3` | Secret name from **Configure S3 Secrets** |
| `s3-script-key` | `workflows/sklearn-iris-training/train_iris.py` | Object uploaded in step 3 |
| `s3-output-prefix` | `workflows/sklearn-iris-training/runs` | Parent prefix for run outputs |


For HTTPS with a custom CA, add the certificate in step 4 and uncomment
`caSecret` in **both** `s3` blocks in `workflow.yaml`:

```yaml
insecure: false
caSecret:
  name: "{{inputs.parameters.s3-credentials-secret}}"
  key: ca.pem
```

Argo uses the certificate from the same S3 credentials Secret to verify artifact
downloads and uploads. For publicly trusted certificates, leave `caSecret` omitted.
See [Argo's CA configuration](https://argo-workflows.readthedocs.io/en/release-4.0/configure-artifact-repository/#configuring-minio).

## 6. Submit the Workflow

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

## 7. Check the Outputs

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
