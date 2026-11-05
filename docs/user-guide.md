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
- edit or upload S3 model repository files and folders when S3 is configured (`member`/`admin`)
- update Triton or S3 connection for that instance (`member`/`admin`)
- add Triton instances (`member`/`admin`)
- delete Triton instances (`admin` only)

If an expected instance is missing, an administrator must assign that instance
to the user account.

## Account Activation and Password Recovery

For local authentication, administrators do not assign an initial password.
When email lifecycle delivery is enabled, an administrator can create an
explicitly inactive account or issue an invitation. An invitation opens
`/activate-account`, where the recipient sets a policy-compliant password.
Expired, revoked, consumed, and invalid links display the same safe message and
cannot be reused.

When delivery is disabled, an administrator-precreated passwordless local
account can be claimed through matching public self-registration. Registration
sets the initial password and preserves the role and instance assignments
chosen by the administrator.

When SMTP recovery is operational, the login page shows **Forgot password**.
Submitting the form always displays the same confirmation, whether or not the
address is eligible. In installations without outbound email, contact an
administrator. They can issue a one-time reset link in manual-link mode after
verifying your identity.

## Navigation Map

Main sidebar entries:

- Dashboard
- Triton Instances
- Development (when Kubernetes actions are available)
- Add Deployment (when Kubernetes actions are available)
- Workflows (when Kubernetes actions and Argo Workflows are available)
- Add Instance (button, last nav action): creates a manually managed Triton instance entry.
- S3 Profiles (existing account menu entry, `admin`): manage reusable S3 connections.
- Error Logs (admin only)

Within an instance, primary workflows are on the detail page tabs plus dedicated
pages for Inference, Profile, and S3 Browser.

## Dashboard

The dashboard gives a fleet-level view of the instances visible to the current
user. It shows summary cards, a table of Triton instances, and active alerts.

For non-admin users, alerts are limited to their assigned instances. Admins see
alerts across the full fleet.

## Error Logs (Admin Only)

The **Error Logs** page shows recent frontend and backend application errors.
Administrators can filter the list by source and select an event to inspect its
details.

Frontend events are reported only while a user is signed in. They include:

- unhandled Angular or JavaScript runtime errors
- backend API responses with HTTP status `500` or higher
- available context such as the message, stack or response detail, page or API
  path, HTTP method and status, user, and browser user agent

The frontend does not report handled component errors, HTTP `4xx` responses,
plain `console.error()` calls, or errors that occur before sign-in. Error
reporting is best effort; if the reporting request itself fails, it is not
reported again.

Backend events include unhandled request exceptions and log records at
`ERROR` or `CRITICAL` level from application loggers. Sensitive lines containing
common credential markers are redacted, and long values are truncated before
storage.

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
- open the model Perf view
- request model load (`member`/`admin`)
- request model unload (`member`/`admin`)

Load/unload depends on Triton model control mode. Explicit operations require
explicit model control on Triton.

### S3 Connection Tab

Members and admins select an available **S3 profile** and an optional repository
prefix. Administrators configure endpoint, bucket, credentials, region, addressing
mode, and CA certificate in the existing **S3 Profiles** account menu entry.
Manual connection fields are no longer available in the instance dialog.

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
- model configuration side panel
- request latency and response JSON
- per-request inference metrics (when available)

Request target:

```text
/v2/models/<model>/versions/<version>/infer
```

Metrics behavior:

- preferred source: Prometheus `/metrics` delta before/after request
- fallback: Triton `/v2/models/stats`

Configuration side panel behavior:

- if the instance has an active S3 connection, the panel shows the raw
  `config.pbtxt` file for the selected model repository path
- members and admins can edit and save that `config.pbtxt`; viewers can inspect it
- if S3 is not configured, the panel shows the read-only Triton **Live API
  Config** for the selected model/version
- **Live API Config** is Triton's parsed runtime JSON response, not the raw
  `config.pbtxt` file

## Model Perf View

Open **Perf** from a model row to configure and run a benchmark.

Reference:

- NVIDIA Perf Analyzer docs: https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/README.html

Current behavior:

- configure the analyzer image above batch size, concurrency range, and request count
- private registry authentication uses the central credentials configured by an administrator under **Settings → Registry**
- each run creates its own non-root Kubernetes Job; no global installation is required
- different models run concurrently, with one active run per model within an instance across all versions
- while this model is creating, pending, running, or stopping, Start is disabled
- a pulsing speedometer and **Benchmark running** badge indicate the running job state; preparing and stopping have separate indicators, and the badge disappears when the run finishes
- Stop terminates only this run; Start becomes available after confirmed termination
- reopening the view recovers the active run and saved results
- form settings are restored for the selected version; an active run for another version still blocks Start but does not replace the selected version's settings
- previously captured output is retained if final pod logs become unavailable; log retrieval warnings appear in the run status separately from the output
- the side configuration panel follows the same behavior as the Inference page:
  editable S3 `config.pbtxt` when S3 is configured, otherwise read-only
  **Live API Config**

## S3 Browser Page

When S3 is configured and enabled for an instance, the S3 Browser supports:

- browsing folders/files under repository prefix
- creating folders (`member`/`admin`)
- downloading files
- deleting files and folders (`member`/`admin`)
- opening editable files (`.py`, `.pbtxt`) (`member`/`admin`)
- editing and saving `.py` and `.pbtxt` (`member`/`admin`)
- uploading files (`member`/`admin`)
- uploading folders with nested child files (`member`/`admin`)

When saving `config.pbtxt`, backend validation is executed against the Triton
model configuration parser for that Triton version.

The same validation is used when saving `config.pbtxt` from the Inference or
Profile page side panel.

Folder upload preserves the selected folder structure below the current S3
browser path. For example, uploading a local folder that contains
`resnet/1/model.plan` while the browser is open at `/models` writes the object
to `/models/resnet/1/model.plan`.

See [Model Config Validation](model-config-validation.md) for details about
Triton version mapping and protobuf parser generation.

Deleting a file removes that S3 object. Deleting a folder removes all S3
objects below that folder prefix. After a folder delete succeeds, the folder
and its child paths are also removed from the S3 Browser tree.

Current limitation:

- rename and move actions are not available in the current release

## MLflow

The **MLflow** sidebar entry provides a singleton MLflow tracking server managed
by Triton Control. Members and admins can install it in Kubernetes, use its UI
through the authenticated backend proxy, refresh its readiness state, and
uninstall it.

Installation requires an available S3 profile and a Kubernetes resource name.
Private images use the central credentials from **Settings → Registry**. MLflow stores all artifacts in `s3://<bucket>/<profile-prefix>/mlflow`,
with paths allocated per experiment and logged model. Credentials, the endpoint,
path-style configuration, and an optional CA certificate are stored in a
server-only Kubernetes Secret. Workflows upload through the tracking proxy.

New installations use the existing PostgreSQL connection in the isolated
`mlflow_tracking` schema for tracking and registry metadata. The database must
be reachable from the MLflow pod, and its user must be allowed to create that
schema. No MLflow PersistentVolumeClaim is created. The default MLflow image
installs boto3 and psycopg2 into temporary storage if they are missing; use an
image with these dependencies preinstalled in clusters without package-index access.

Profiles used by an installed MLflow cannot be changed or deleted. Uninstalling
removes Kubernetes workloads and Secrets, but retains S3 artifacts and PostgreSQL
tracking data. Reinstall with the same endpoint, bucket and prefix to keep proxy
artifact references valid. SQLite/PVC installations are no longer supported by
the tracking upgrade endpoint. Export their data and migrate to PostgreSQL/S3
before upgrading.

## Registry Credentials

Administrators configure a shared image pull secret under **Settings → Registry**.
Paste a Docker config JSON containing a non-empty `auths` object and choose
**Save secret** to create or replace it. **Remove secret** clears it. The page
shows whether credentials are configured without returning their contents.

New Triton deployments, development workspaces, MLflow installations, and model
performance Jobs use these credentials automatically. Per-workload credential
overrides are rejected. Updating or removing the central secret does not modify
existing Kubernetes workloads; recreate the affected workload to apply a change.
Helm-managed Argo components and Workflow YAML retain their separate image pull
Secret configuration described in [Argo Workflows](argo-workflows.md).

### Using Private and Public Registries Together

You can deploy images from a private registry and public registries with the
same global pull secret. Include credentials only for registries that require
authentication. Kubernetes selects credentials that match the image's registry
and any configured path prefix; it does not send them to unrelated registries.

For example, if the secret contains credentials only for `registry.example.com`:

| Image | Pull behavior |
| --- | --- |
| `registry.example.com/team/triton:release` | Uses the configured private registry credentials. |
| `docker.io/library/nginx:stable` | Pulls anonymously because the secret has no matching Docker Hub entry. |

No per-deployment switch is needed. If all images are public, leave the global
secret unset.

A public repository on the same registry can also match the configured
credentials. Kubernetes then attempts authentication even if anonymous access
is allowed; invalid credentials can cause the pull to fail. Keep matching
credentials valid rather than relying on an anonymous fallback. See
[Kubernetes credential matching](https://kubernetes.io/docs/concepts/containers/images/#interpretation-of-config-json).

## S3 Profiles

Administrators manage reusable S3 deployment profiles through the existing
**S3 Profiles** account menu entry. Members select these profiles in deployment, instance connection, MLflow,
workflow, and code-server flows. A profile stores:

- profile name
- endpoint
- bucket
- optional repository parent prefix
- region
- access key and stored secret key
- path-style mode
- optional CA certificate

Profiles owned by administrators are available to members. Existing personal
profiles remain available to their owners; administrators can list, edit, and
delete those legacy profiles too. Only administrators can create or change
profile configuration. Browser API responses omit secret keys. The code-server **Triton Control Deploy** extension uses
these profiles for its native Explorer S3 browser and model deployments.
Connect from **Explorer → S3 Operations → Choose Profile…** on a workspace
item; the browser uses the workspace owner's saved credentials and CA
certificate dynamically. The deployment form requires a profile; manual S3
credential entry is removed.

The stored profile values are used to upload the selected model repository from
the code-server workspace and to create the Triton deployment. The resulting
deployment still receives its own Kubernetes Secret for the in-pod S3
repository connection.

## Add Deployment (Sidebar Entry)

**Add Deployment** is a standalone navigation entry, not an instance detail tab.
It creates a self-deployed Triton workload on Kubernetes.

Prerequisite: an S3-compatible object store and bucket must already exist. The
bucket, or the optional repository prefix within it, must contain models in the
directory structure expected by Triton Inference Server.

In vLLM sync modes, Triton's repository path must always be the directory above
the model folder. For example, version files under `modela/1` are published as
`/models/modela/1`, while Triton receives `--model-repository=/models`. When a
vLLM S3 URL points directly at a single folder containing `config.pbtxt`, the
sync worker adds the model-name directory automatically.

Select an available **S3 profile** during **Add Deployment**. The backend
resolves its connection settings and writes them into the Kubernetes deployment.
**Repository prefix** is the optional path inside the profile bucket, and the
form shows a **Serving repository** preview. Requests carry the profile ID and
prefix instead of connection credentials; manual S3 settings are rejected.

Models that do not use the vLLM backend use Triton's native S3 model repository
directly. Enable **vLLM model backend** only when deploying vLLM models; Triton
Control then uses the vLLM repository sync path internally and Triton reads the
stable `/models` path. Relative `model` and `tokenizer` values in vLLM
`model.json` files are changed to absolute paths under that directory. After
the deployment is created, Triton Control cannot change that in-pod S3
repository connection in place. To change it, delete the self-deployed Triton
instance and create a new deployment with the new S3 settings.

The **S3 Connection** tab on an instance is different: it configures how Triton
Control connects to S3 for browsing, editing, and uploading model repository
files. Editing that S3 connection changes Triton Control's backend access to
S3; it does not rewrite the S3 connection used by the already running Triton
pod.

| Field                            | Required              | Purpose                                                                                                  | Recommended usage                                                                                                                                    |
| -------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deployment name                  | Yes                   | Base name for Kubernetes resources (deployment/service/secret) and, in external backend mode, namespace. | Keep stable and DNS-safe.                                                                                                                            |
| Image                            | Yes                   | Triton server container image to run.                                                                    | Pin explicit version tags in stage/prod.                                                                                                             |
| S3 profile                       | Yes                   | Available admin-managed or existing personal S3 profile.                                                                  | Ask an administrator to configure missing profiles.                                                                                                            |
| Repository prefix                | Optional              | S3 path prefix inside the selected bucket.                                                               | Leave empty when models are stored at the bucket root.                                                                                               |
| Serving repository | Read-only             | Preview of the final S3 repository URL.                                                                  | Confirm the selected bucket and prefix before deploy.                                                                                           |
| vLLM model backend               | Optional              | Enables vLLM repository handling for vLLM models.                                                        | Use only when deploying vLLM models.                                                                                                                 |
| Model control mode               | Yes                   | Triton behavior (`explicit` or `poll`).                                                                  | Use mode based on model operation strategy.                                                                                                          |
| Startup model                    | Optional (`explicit`) | Model loaded at startup (`*` if empty).                                                                  | Control startup footprint when needed.                                                                                                               |
| Poll interval                    | Optional (`poll`)     | Seconds between repository checks.                                                                       | Tune for change rate and API load.                                                                                                                   |
| Ingress host                     | Optional              | Expose deployment through administrator-managed Traefik.                                                                       | Use for external cluster access.                                                                                                                     |
| `requirements.txt`               | Optional              | Extra Python packages installed before Triton start.                                                     | Prefer dev/stage; bake into image for production.                                                                                                    |
| Resources (GPU/CPU/Memory)       | Optional              | Kubernetes GPU limit and CPU/memory resource settings.                                                   | Enter memory as a whole Gi number; the form adds the `Gi` unit. Set GPU count to at least `1` for vLLM; the form defaults this when vLLM is enabled. |

For private container registries, an administrator saves Docker registry
authentication JSON under **Settings → Registry**. The following JSON is only
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

- The administrator selects the Traefik class, entrypoints and certificate policy.
- If a cert-manager ClusterIssuer is configured, HTTPS Ingresses automatically request
  their own certificates; otherwise they use an existing TLS Secret or Traefik's
  default certificate.
- Users choose the hostname and access mode, not the controller or certificate issuer.
- See [Traefik and HTTPS setup](deployment.md#administrator-managed-traefik-and-https).

When ingress is not configured, Triton Control uses internal service DNS:

```text
http://<service>.<namespace>.svc.cluster.local:<port>
```

## Development Workspace (Sidebar Entry)

**Development** is a standalone product feature that creates a private,
Kubernetes-backed code-server workspace for the signed-in user. It is separate
from development of the Triton Control application itself.

Users can create one persistent workspace, edit model repositories under
`/workspace`, and deploy them through the bundled **Triton Control Deploy**
extension.

For S3 files, use the native **Explorer** alongside `/workspace`. Right-click a
workspace item and choose **S3 Operations → Choose Profile…**. Connected menus
provide **Refresh**, **Switch Profile…**, and **Disconnect**. Dragging between
the workspace and S3 copies; dragging within one S3 profile moves. Ctrl-drag
forces copy and Shift-drag forces move. The S3 browser needs no webview or
manual connection URL. See [S3 transfers](development-workspaces.md#transfer-files-in-explorer)
for folder behavior and limits.

For model scaffolding, open the Triton Control Activity Bar view and select
**New Model Repository** to create a starter Triton repository. The command is
also available from the command palette and Explorer folder context menu. It
can create a single-model repository from Python, ONNX Runtime, TensorRT,
TensorRT-LLM, vLLM, or PyTorch/LibTorch templates, or an ensemble pipeline
with child model folders and an ensemble `config.pbtxt`.

Generated `config.pbtxt` files are templates. Review the model artifacts,
input/output tensor names, shapes, and data types before deploying. The
Triton Control Activity Bar view marks discovered repositories as
**review model setup** until scaffold placeholders or generic config template
values are removed. Selecting a repository row switches to Explorer and
reveals the real workspace folder; **ready to deploy** repositories expose
deploy actions in the view and context menu.

The deploy extension has a full webview form for trusted HTTPS or localhost.
For plain HTTP or an untrusted local certificate, use
**Triton Control: Upload Model Repository (Simple Wizard)** in code-server.
The simple wizard avoids webviews, uploads the repository to S3, and shows the
values needed to finish in **Add Deployment**.

See [Development Workspaces](development-workspaces.md) for workspace fields,
Kubernetes resources, persistence, proxy behavior, deployment steps, and API
details.

## Perf Analyzer Job Configuration

Performance controls are available from each model's **Perf** view. There is no
global Perf Analyzer sidebar entry or installation step. See
[Model performance Jobs](model-performance-jobs.md) for lifecycle, API, security,
and upgrade details.

Namespace behavior:

- each Perf Analyzer Job is created in the same namespace as Triton Control
- in Kubernetes, this is the namespace of the running Triton Control pod
- outside Kubernetes, the namespace defaults to `triton-control` and can be
  overridden with `TRITON_CONTROL_NAMESPACE`, `KUBERNETES_NAMESPACE`, or
  `POD_NAMESPACE`

Current scope:

- Add Deployment and Perf Analyzer workflows are intended for development/stage
- they are not positioned as full production orchestration in this version

Perf Analyzer automatically uses the central image pull secret from
**Settings → Registry** when creating a benchmark Job. There is no per-run
registry credential field. This example is not a
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

## Workflows (Sidebar Entry)

**Workflows** embeds the single global Argo Workflows server installed by the
Triton Control Helm release. The browser connects only to Triton Control:

```text
/api/workflows/proxy/
```

The backend authenticates member/admin access and proxies Argo UI, API,
streaming, and WebSocket traffic to the internal Argo Server `ClusterIP`
Service. The Argo Service is not exposed directly to the browser.

When `argoWorkflows.enabled=false`, the page shows that the feature is disabled.
When enabled but the Argo Server is unavailable, it displays the readiness
error instead of loading the iframe.

Workflows run in the same Kubernetes namespace as Triton Control. Images
referenced by uploaded Workflow YAML are separate from the public Argo system
images. Private Workflow images require a pull Secret in that namespace.

Use **Configure S3 Secrets** to link a saved S3 profile for workflow artifacts.
Triton Control synchronizes connection settings into a ConfigMap and credentials
and the optional CA into a Secret. When exactly one of your profiles is linked,
Triton Control automatically selects it for workflows with S3 artifact keys.
With multiple linked profiles, specify the profile name in the workflow annotation
`triton-control.ai/mlflow-s3-profile-name`. No ConfigMap or Secret names are needed.
An explicit `artifactRepositoryRef` remains available for direct Argo submissions.
See [S3 profile linking and synchronization](argo-workflows.md#s3-credentials).

## Add Instance (Sidebar Action)

**Add Instance** is a dedicated sidebar action (shown as the last nav action).
It is used to register an already running Triton endpoint in Triton Control.

Use this path when:

- Triton is already deployed outside Triton Control
- you want monitoring, model actions, inference, and profile workflows in the UI

Behavior after creation:

- the instance appears in **Triton Instances** like other instances
- **Perf** can be opened from the **Models** tab without a global analyzer installation
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

| SSL Flag                  | Meaning                                                               |
| ------------------------- | --------------------------------------------------------------------- |
| Off                       | No certificate validation (only for local/trusted test environments). |
| On, no certificate pasted | Validate with system trust store.                                     |
| On, certificate pasted    | Validate with pasted PEM CA certificate.                              |

The certificate field expects a PEM CA certificate, not a private key.

## HTTPS S3 Connections

S3 endpoint example:

```text
https://s3.example.com
```

SSL flag semantics:

| SSL Flag                  | Meaning                                                               |
| ------------------------- | --------------------------------------------------------------------- |
| Off                       | No certificate validation (only for local/trusted test environments). |
| On, no certificate pasted | Validate with system trust store.                                     |
| On, certificate pasted    | Validate with pasted PEM CA certificate.                              |

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
