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

## 4. Link the S3 Profile

In **Workflows → Configure S3 Secrets**, select your **S3 profile** and click
**Link profile**. Triton Control creates a Secret and an Argo repository ConfigMap
in the workflow namespace. Endpoint, bucket, region, credentials, and the optional
HTTPS CA certificate sync automatically when the profile is saved.

For an existing link, click **Sync now** once after upgrading to create its repository.
The dialog shows sync errors and lets the profile owner retry. Remove the link before
deleting its source profile. New runs use updated settings; running workflows may
retain previously loaded values.

## 5. Configure the Workflow in the Workspace

Open `/workspace/sklearn-iris-training/workflow.yaml`. Copy the
`artifactRepositoryRef` shown in the dialog under `spec`:

```yaml
artifactRepositoryRef:
  configMap: workflow-s3-training-a1b2c3 # Use your generated name.
  key: repository
```

Submit in the namespace shown in the dialog. No endpoint, bucket, region, credentials,
or certificate settings need to be repeated in the workflow.
Argo uses [key-only artifacts](https://argo-workflows.readthedocs.io/en/latest/key-only-artifacts/)
with the linked repository. Set only the object paths in `spec.arguments.parameters`:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `s3-script-key` | `workflows/sklearn-iris-training/train_iris.py` | Object uploaded in step 3 |
| `s3-output-prefix` | `workflows/sklearn-iris-training/runs` | Parent prefix for run outputs |

Object paths are full bucket keys; the profile's browsing prefix is not added.
For HTTPS with a custom CA, save the public CA certificate in the S3 profile;
Triton Control adds Argo's certificate reference automatically.

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
