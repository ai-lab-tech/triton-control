# Triton Control Deploy Extension

This code-server extension uploads a selected Triton model repository folder to
S3/MinIO, then calls Triton Control's existing `POST /api/deployments` endpoint.

## Flow

1. Right-click a Triton model repository folder in code-server.
2. Run `Triton Control: Deploy Model Repository`.
3. The extension detects the model name from `config.pbtxt`. If no model name is
   found, it asks for one.
4. Confirm S3 and deployment settings. The deployment name is filled from the
   model name.
5. The extension uploads files to `bucket/prefix/deployment-name`.
6. The webview calls `/api/deployments` with the current Triton Control browser
   session, so the normal Add Deployment path is reused.

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
code-server workspace.

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
