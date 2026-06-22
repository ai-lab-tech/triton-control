# Triton Control Deploy Extension

This code-server extension uploads a selected Triton model folder or Triton
model repository root to S3-compatible storage, then calls Triton Control's
existing `POST /api/deployments` endpoint.

## Flow

1. Right-click a Triton model folder or model repository root in code-server.
2. Run `Triton Control: Deploy Model Repository`.
3. The extension detects the model name from `config.pbtxt`. If no model name is
   found, it asks for one. It also detects `backend: "vllm"`.
4. Confirm S3 and deployment settings. The deployment name is filled from the
   model name.
5. The extension uploads files below `bucket/prefix/deployment-name`.
6. The webview calls `/api/deployments` with the current Triton Control browser
   session, so the normal Add Deployment path is reused.

Repository access defaults are backend-specific:

- non-vLLM configurations select native Triton S3 (`direct`) and create no sync
  container;
- vLLM configurations select the polling sidecar for the development workflow;
- the form can switch vLLM to init-container mode for stage/production.

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
bucket/prefix/deployment-name/model-name/config.pbtxt
bucket/prefix/deployment-name/model-name/1/model.py
```

The deployment `s3_url` points at the repository root:

```text
s3://<endpoint>/<bucket>/prefix/deployment-name
```

It does not point at `.../model-name`, because that would make Triton start one
directory too deep.

## Settings

Configure these in code-server settings or environment variables:

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

When required S3 values are missing and the extension prompts for them, it saves
the answers to `tritonControlDeploy.*` settings for the next deploy in the same
code-server workspace. In Triton Control-managed workspaces, those settings are
backed by `/workspace/.triton-control/code-server-settings.json` so they survive
pod restarts. Extensions installed from the code-server UI are stored at
`/workspace/.triton-control/code-server-extensions`, so S3/R2 Explorer and other
live-installed extensions survive workspace pod restarts too.

The deploy form also saves reusable values on submit, including
`tritonControlDeploy.s3Prefix`, `tritonControlDeploy.s3CaCertificate`,
`tritonControlDeploy.s3ForcePathStyle`, and `tritonControlDeploy.tritonImage`.
That means a pasted S3 CA certificate is pre-filled for the next deployment from
the same workspace.

The same submit action also syncs compatible S3/R2 Explorer settings
(`s3x.endpointUrl`, `s3x.region`, `s3x.accessKeyId`, `s3x.secretAccessKey`,
and `s3x.forcePathStyle`). Local, MinIO, minikube, `.local`, `.internal`, and
IP endpoints are forced to path-style addressing to avoid bucket-prefixed DNS
lookups such as `bucket.host.minikube.internal`.

Keep `tritonControlDeploy.s3ForcePathStyle` enabled for providers and custom
endpoints that require path-style bucket URLs. Disable it only when your S3
provider requires virtual-host bucket URLs.

For Triton deployments using HTTPS S3 endpoints, paste the optional S3 CA
certificate into the deploy form. It is passed to Triton Control as
`s3_ca_certificate` so the Triton pod trusts the object store.

## Package

From this directory:

```powershell
npm install
npm run package
```

Install the generated `.vsix` in code-server.
