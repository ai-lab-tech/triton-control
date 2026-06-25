# Triton Control Deploy Extension

This code-server extension uploads a selected Triton model folder or Triton
model repository root to S3-compatible storage, then calls Triton Control's
existing `POST /api/deployments` endpoint.

## Flow

1. Right-click a Triton model folder or model repository root in code-server.
2. Run `Triton Control: Deploy Model Repository`.
3. The extension detects the model name and `backend` value from `config.pbtxt`.
   If no model name is found, it asks for one. When no backend is declared, the
   form shows `No backend in config.pbtxt`.
4. Select an S3 profile or expand manual S3 settings. The **Repository prefix**
   is an optional parent path; the upload target preview shows the final
   `s3://...` path before deploy.
5. The extension uploads files below `bucket/prefix/model-repository-path`.
6. The webview calls `/api/deployments` with the current Triton Control browser
   session, so the normal Add Deployment path is reused.

Repository access is selected automatically. Models without `backend: "vllm"`
use Triton's native S3 model repository directly. vLLM models use the sync
worker internally so local paths in `model.json` work without extra user input.

Triton expects the model repository root to contain one folder per model:

The deployed Triton repository argument always points one directory above the
model folder. For example, `modela/1/model.json` is materialized below
`/models/modela/1`, and Triton uses `/models` as its repository root.

```text
repository-root/
  model-name/
    config.pbtxt
    1/
      model.py
```

If you select the `model-name` folder directly, the extension uploads it as:

```text
bucket/prefix/model-repository-path/model-name/config.pbtxt
bucket/prefix/model-repository-path/model-name/1/model.py
```

The deployment `s3_url` points at the repository root:

```text
s3://<endpoint>/<bucket>/prefix/model-repository-path
```

It does not point at `.../model-name`, because that would make Triton start one
directory too deep.

## S3 Profiles

Members and admins can manage reusable S3 profiles in Triton Control from the
account menu. The extension loads those profiles from `/api/s3-profiles` and
shows them in the **S3 profile** dropdown.

Each profile stores:

- profile name
- S3-compatible endpoint
- bucket
- optional repository parent prefix
- region
- access key and encrypted secret key
- path-style mode
- optional CA certificate

The selected profile is used both for uploading the chosen repository from
code-server and for creating the Triton deployment. The deployment receives its
own Kubernetes Secret for the in-pod S3 repository connection.

When a profile is selected, manual S3 connection fields remain collapsed. For
manual S3 deployments, enter the endpoint, bucket, credentials, optional
repository prefix, and confirm the **Target path** preview at the end of the
manual section.

## Manual S3 Settings

Manual S3 settings remain available in a collapsed section for one-off
deployments or for saving a new profile from the extension. Defaults can still
come from code-server settings or environment variables:

- `tritonControlDeploy.s3Endpoint` or `AWS_ENDPOINT_URL` / `S3_ENDPOINT`
- `tritonControlDeploy.s3Bucket` or `S3_BUCKET`
- `tritonControlDeploy.s3Prefix` or `S3_PREFIX`
- `tritonControlDeploy.s3Region` or `AWS_REGION`
- `tritonControlDeploy.s3AccessKeyId` or `AWS_ACCESS_KEY_ID`
- `tritonControlDeploy.s3SecretAccessKey` or `AWS_SECRET_ACCESS_KEY`

If S3/R2 Explorer is configured, this extension reuses:

- `s3x.endpointUrl`
- `s3x.region`
- `s3x.accessKeyId`
- `s3x.secretAccessKey`
- `s3x.forcePathStyle`

In Triton Control-managed workspaces, code-server settings are backed by
`/workspace/.triton-control/code-server-settings.json` so they survive pod
restarts. Extensions installed from the code-server UI are stored at
`/workspace/.triton-control/code-server-extensions`.

Local, MinIO, minikube, `.local`, `.internal`, and IP endpoints should use
path-style addressing to avoid bucket-prefixed DNS lookups such as
`bucket.host.minikube.internal`.

Keep `tritonControlDeploy.s3ForcePathStyle` enabled for providers and custom
endpoints that require path-style bucket URLs. Disable it only when your S3
provider requires virtual-host bucket URLs.

For Triton deployments using HTTPS S3 endpoints, paste the optional S3 CA
certificate into the S3 profile or manual deploy form. It is passed to Triton
Control as `s3_ca_certificate` so the Triton pod trusts the object store.

## Detected Backend and Model Control

The deploy form shows detected backend and model control as summary values, not
editable inputs. Backend is read from `config.pbtxt`:

- `backend: "vllm"` is shown as `vLLM model backend`.
- Other backend values are shown as `<backend> model backend`.
- Missing backend is shown as `No backend in config.pbtxt`.

Model control uses **Polling mode** by default. **Poll interval seconds** is
shown only in polling mode. Switch to **Explicit mode** when the deployment
should load only the configured startup model.

## Resources

The deploy form has a collapsed **Resources** section. The default values shown
there are real form values and are sent with the deployment:

- CPU: `2`
- RAM: `4Gi`
- GPU count: `1`

Change or clear these values before deploy if the model needs different
resources. Values present in the form are sent to Triton Control for every
backend.

## Package

From this directory:

```powershell
npm install
npm run package
```

Install the generated `.vsix` in code-server.
