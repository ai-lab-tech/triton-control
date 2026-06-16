# User Guide

This page describes what a regular Triton Control user can do after signing in.
Administrative setup, OIDC configuration, and user management are covered in
[User Management](user-management.md) and the other admin documentation pages.

## Access Model

Users see only the Triton instances assigned to them. Administrators can see all
instances and can also open user and settings pages.

For each assigned instance, viewers can inspect and use read workflows.
Members and admins can additionally perform write workflows.

- view dashboard and instance health
- inspect Triton server metadata
- view model repository state
- run model inference
- browse and download S3 model repository files when S3 is configured
- request model load or unload operations (`member`/`admin`)
- edit or upload S3 model repository files when S3 is configured (`member`/`admin`)
- update Triton or S3 connection for that instance (`member`/`admin`)
- add Triton instances (`member`/`admin`)
- delete Triton instances (`admin` only)

If an expected instance is missing, an administrator must assign that instance
to the user account.

## Navigation Map

Main sidebar entries:

- Dashboard
- Triton Instances
- Code Servers (when Kubernetes actions are available)
- Add Deployment (when Kubernetes actions are available)
- Perf Analyzer (when Kubernetes actions are available)
- Add Instance (button, last nav action): creates a manually managed Triton instance entry.

Within an instance, primary workflows are on the detail page tabs plus dedicated
pages for Inference, Profile, and S3 Browser.

## Dashboard

The dashboard gives a fleet-level view of the instances visible to the current
user. It shows summary cards, a table of Triton instances, and active alerts.

For non-admin users, alerts are limited to their assigned instances. Admins see
alerts across the full fleet.

## Triton Instances

### Instance List

The instance list shows each visible Triton instance with:

- instance name and URL
- region when configured
- health status
- number of models
- CPU, RAM, and GPU metrics when a metrics endpoint is configured

Use search and status filters to find an instance. Open **Details** to inspect
runtime status, metadata, models, and S3 configuration.

### Metrics Behavior

CPU, RAM, and GPU values are shown only when the instance has a metrics endpoint
configured and Triton exposes usable Prometheus metrics.

- if no metrics endpoint is configured, the UI shows `N/A`
- if the endpoint is configured but cannot be read, the detail page shows a metrics error

Example metrics endpoint:

```text
http://triton-server:8002/metrics
```

If the configured URL has no path, the backend appends `/metrics`.

## Instance Details

The instance detail page is tab-based. Depending on instance type and
permissions, you will see:

- Overview
- Models
- S3 Connection
- Logs (self-deployed instances)

For instances added via **Add Instance** (existing Triton endpoints), profiling
can still be executed from the **Models** tab using **Profile** when Perf
Analyzer is installed.

For these manually added instances, the **Logs** tab is not available.

### Overview Tab

The overview tab shows:

- status, version, runtime environment, and assigned users
- Triton base URL and optional metrics URL
- SSL verification status
- `/v2/health/live` and `/v2/health/ready` results
- Triton `/v2` metadata as summary or raw JSON

Members and admins can update Triton endpoint, metrics endpoint, and SSL
certificate verification settings from the **Edit** action in the **Networking**
card header.

### Models Tab

The models tab shows the live Triton repository index.

If the instance is unhealthy or unreachable, Triton Control cannot read the live
repository index and the tab may show no models. Check the **Overview** tab
health details and connection errors first.

For each model version, users can:

- see model state and reason returned by Triton
- open the inference page
- open the profile page (when Perf Analyzer is installed)
- request model load (`member`/`admin`)
- request model unload (`member`/`admin`)

Load/unload depends on Triton model control mode. Explicit operations require
explicit model control on Triton.

### S3 Connection Tab

Members and admins can configure S3 endpoint, bucket, credentials, optional
region/prefix, SSL verification, and optional CA certificate.

When S3 is active, users can open the S3 Browser from the detail page.

For self-deployed instances created with **Add Deployment**, this tab controls
Triton Control's S3 browser/editor connection only. It does not update the S3
model repository connection inside the already running Triton pod.

### Logs Tab (Self-Deployed)

Self-deployed instances expose a Logs tab for Kubernetes deployment events and
recent Triton pod output.

## Model Inference Page

Open **Infer** from a model row to send inference through the backend proxy.

The inference page shows:

- target instance and model version
- resolved Triton infer URL
- JSON request body editor
- model API config via **Show API Config**
- request latency and response JSON
- per-request inference metrics (when available)

Request target:

```text
/v2/models/<model>/versions/<version>/infer
```

Metrics behavior:

- preferred source: Prometheus `/metrics` delta before/after request
- fallback: Triton `/v2/models/stats`

## Model Profile Page (Perf Analyzer)

Open **Profile** from a model row to run model profiling.

Reference:

- NVIDIA Perf Analyzer docs: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/README.html

Current behavior:

- profile button appears only when Perf Analyzer installation exists
- only one profile run can execute at a time
- while one run is active, other profile actions are disabled
- active run can be reopened to inspect progress/results

## S3 Browser Page

When S3 is configured and enabled for an instance, the S3 Browser supports:

- browsing folders/files under repository prefix
- downloading files
- opening editable files (`.py`, `.pbtxt`) (`member`/`admin`)
- editing and saving `.py` and `.pbtxt` (`member`/`admin`)
- uploading files (`member`/`admin`)

When saving `config.pbtxt`, backend validation is executed against the Triton
model configuration parser for that Triton version.

See [Model Config Validation](model-config-validation.md) for details about
Triton version mapping and protobuf parser generation.

Current limitation:

- rename, move, and delete actions are not available in the current release

## Add Deployment (Sidebar Entry)

**Add Deployment** is a standalone navigation entry, not an instance detail tab.
It creates a self-deployed Triton workload on Kubernetes.

Prerequisite: an S3-compatible object store and bucket must already exist. The
bucket path used as the model repository must contain models in the directory
structure expected by Triton Inference Server.

The S3 settings entered during **Add Deployment** are written into the
Kubernetes deployment and are consumed from inside the Triton pod as the model
repository connection. After the deployment is created, Triton Control cannot
change that in-pod S3 repository connection in place. To change it, delete the
self-deployed Triton instance and create a new deployment with the new S3
settings.

The **S3 Connection** tab on an instance is different: it configures how Triton
Control connects to S3 for browsing, editing, and uploading model repository
files. Editing that S3 connection changes Triton Control's backend access to
S3; it does not rewrite the S3 connection used by the already running Triton
pod.

| Field | Required | Purpose | Recommended usage |
| --- | --- | --- | --- |
| Deployment name | Yes | Base name for Kubernetes resources (deployment/service/secret) and, in external backend mode, namespace. | Keep stable and DNS-safe. |
| Image | Yes | Triton server container image to run. | Pin explicit version tags in stage/prod. |
| S3 URL | Yes | Triton model repository path passed to `--model-repository`. | Use valid S3-compatible repository path. |
| S3 CA certificate | Optional | PEM CA certificate for HTTPS S3 endpoints. | Provide for private/self-signed/internal CAs. |
| Access key / Secret key / Region | Yes | Repository credentials and region. | Use least-privilege credentials. |
| Model control mode | Yes | Triton behavior (`explicit` or `poll`). | Use mode based on model operation strategy. |
| Startup model | Optional (`explicit`) | Model loaded at startup (`*` if empty). | Control startup footprint when needed. |
| Poll interval | Optional (`poll`) | Polling interval in seconds. | Tune for change rate and API load. |
| Ingress host/class | Optional | Expose deployment through ingress. | Use for external cluster access. |
| `.dockerconfigjson` | Optional | Private registry pull credentials. | Required for private images. |
| `requirements.txt` | Optional | Extra Python packages installed before Triton start. | Prefer dev/stage; bake into image for production. |
| Resources (GPU/CPU/Memory) | Optional | Kubernetes resource requests. CPU and memory limits are set to the same values as the requests. | Strongly recommended in stage/prod. |

For private container registries, paste Docker registry authentication JSON into
the image pull secret field (`.dockerconfigjson`). The following JSON is only
an example of the expected Docker config shape; replace it with the registry,
credentials, and optional email value for your own registry:

```json
{
  "auths": {
    "<REGISTRY_HOST>": {
      "auth": "<BASE64_USERNAME_COLON_TOKEN>",
      "email": "<EMAIL>"
    }
  }
}
```

`auth` is the base64 encoding of `username:token` or `username:password`.

Ingress/TLS note:

- Add Deployment does not create TLS certificates
- configure TLS separately via ingress controller (for example cert-manager or existing TLS Secret)

When ingress is not configured, Triton Control uses internal service DNS:

```text
http://<service>.<namespace>.svc.cluster.local:<port>
```

## Deploy From Code Server

Code-server workspaces include the **Triton Control Deploy** extension. It lets
members and admins deploy a Triton model repository directly from the workspace:

1. Open **Code Servers** and create or open a workspace.
2. Create or edit a Triton model repository in `/workspace`. The repository
   should follow Triton's model layout, for example:

   ```text
   repository-root/
     model-name/
       config.pbtxt
       1/
         model.py
   ```

3. Right-click either the `repository-root` folder or a single `model-name`
   folder and run
   **Triton Control: Deploy Model Repository**.
4. The extension reads the model name from `config.pbtxt`. If no name is found,
   it asks for one.
5. Confirm the S3-compatible endpoint, bucket, optional prefix, credentials,
   Triton image, model control mode, and optional S3 CA certificate.
6. The extension uploads the repository to S3-compatible storage and calls the
   same deployment API used by **Add Deployment**. If a single `model-name`
   folder is selected, it is uploaded below the deployment prefix as
   `model-name/config.pbtxt`; the deployment `s3_url` still points at the
   parent repository prefix.
7. Triton Control switches to the new instance detail page and opens deployment
   logs.

The extension reuses S3/R2 Explorer settings when they are configured in
code-server:

| S3/R2 Explorer setting | Used for |
| --- | --- |
| `s3x.endpointUrl` | S3-compatible endpoint |
| `s3x.region` | S3 region |
| `s3x.accessKeyId` | access key |
| `s3x.secretAccessKey` | secret key |
| `s3x.forcePathStyle` | path-style request mode |

If required S3 values are missing, the extension prompts for them and saves the
answers in the code-server workspace settings for the next deploy. Bucket and
prefix are deployment choices: use the bucket that should hold model
repositories, and use the prefix to group uploaded deployments.

For HTTPS S3 endpoints with private or self-signed certificates, paste the PEM
CA certificate into **S3 CA certificate for Triton HTTPS access**. The extension
uses that value in the deployment request so the Triton pod can trust the object
store. The extension's own upload path is intended for workspace-side use and
does not replace the Triton pod CA configuration.

Path-style S3 means the bucket is part of the URL path, for example
`https://s3.example.com/bucket/key`. Virtual-host style means the bucket is part
of the hostname, for example `https://bucket.s3.example.com/key`. Keep
path-style enabled for S3-compatible or custom endpoints that require it.

The deployment created by the extension behaves like any other self-deployed
Triton instance. Its in-pod S3 repository connection is fixed at deployment
time; changing it later requires deleting and recreating the deployment.

Deployment note:

- In the Docker image, the extension source is bundled at
  `/opt/code-server-extensions/triton-deploy` and is installed automatically
  into newly created code-server workspaces.
- For local backend development outside the image, set
  `TRITON_DEPLOY_CODE_SERVER_EXTENSION_DIR` if the backend cannot find the
  repository's `code-server-extensions/triton-deploy` directory automatically.
- Existing code-server pods must be recreated to receive extension changes.

## Perf Analyzer (Sidebar Entry)

**Perf Analyzer** is a standalone navigation entry, not an instance detail tab.

Triton Control supports a shared Perf Analyzer installation for profile runs.

- one installation is managed at a time (singleton installation)
- profile runs target selected instance/model/version from the Profile page

Namespace behavior:

- backend in Kubernetes: self-deployed Triton and Perf Analyzer are created in
  the same namespace as Triton Control
- backend outside Kubernetes: namespace handling remains name-based

Current scope:

- Add Deployment and Perf Analyzer workflows are intended for development/stage
- they are not positioned as full production orchestration in this version

Perf Analyzer can also use a private registry image pull secret. Paste the same
`.dockerconfigjson` format into the Perf Analyzer image pull secret field when
the configured SDK image is stored in a private registry. This example is not a
fixed template; use the Docker config JSON required by your registry:

```json
{
  "auths": {
    "<REGISTRY_HOST>": {
      "auth": "<BASE64_USERNAME_COLON_TOKEN>",
      "email": "<EMAIL>"
    }
  }
}
```

## Add Instance (Sidebar Action)

**Add Instance** is a dedicated sidebar action (shown as the last nav action).
It is used to register an already running Triton endpoint in Triton Control.

Use this path when:

- Triton is already deployed outside Triton Control
- you want monitoring, model actions, inference, and profile workflows in the UI

Behavior after creation:

- the instance appears in **Triton Instances** like other instances
- **Profile** can be used from the **Models** tab when Perf Analyzer is installed
- the **Logs** tab is not shown for these manually added instances

## HTTPS Triton Connections

Set Triton endpoint in **Edit Triton connection**:

```text
https://triton.example.com:8000
```

Metrics endpoint can also use HTTPS:

```text
https://triton.example.com:8002/metrics
```

SSL flag semantics:

| SSL Flag | Meaning |
| --- | --- |
| Off | No certificate validation (only for local/trusted test environments). |
| On, no certificate pasted | Validate with system trust store. |
| On, certificate pasted | Validate with pasted PEM CA certificate. |

The certificate field expects a PEM CA certificate, not a private key.

## HTTPS S3 Connections

S3 endpoint example:

```text
https://s3.example.com
```

SSL flag semantics:

| SSL Flag | Meaning |
| --- | --- |
| Off | No certificate validation (only for local/trusted test environments). |
| On, no certificate pasted | Validate with system trust store. |
| On, certificate pasted | Validate with pasted PEM CA certificate. |

S3 configuration also requires bucket, access key, secret key, optional region,
and optional repository prefix.

## Common Limits

Some actions depend on Triton capabilities and instance configuration:

- metrics require a reachable metrics endpoint
- inference requires ready model version and valid inference payload
- load/unload requires Triton explicit model control
- S3 editing is limited to `.py` and `.pbtxt` files
- instance visibility depends on assigned instances

If an action fails, check the UI error message first. It usually contains the
backend or Triton error returned for that operation.
