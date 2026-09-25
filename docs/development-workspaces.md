# Development Workspaces

The **Development** sidebar entry provides each user with a private,
browser-based development workspace for Triton model repositories. The
workspace UI is powered by code-server and is embedded directly in Triton
Control.

## Prerequisites

Development workspaces are available only when Kubernetes support is enabled.
The sidebar entry is disabled when Triton Control cannot perform Kubernetes
actions.

Each authenticated user can own one workspace. Workspaces are isolated by
ownership: the backend permits users to list, open, proxy, refresh, and delete
only their own workspace.

The Kubernetes cluster must provide:

- a default or otherwise matching StorageClass for the workspace PVC
- permission for Triton Control to create StatefulSets, Services, ConfigMaps,
  Secrets, PVCs, and optional image pull Secrets
- internet access from the workspace pod when code-server or marketplace
  extensions must be downloaded
- NVIDIA GPU scheduling support when a GPU count is requested

## Create a Workspace

Open **Development** in the sidebar and configure the workspace:

| Field | Required | Default | Purpose |
| --- | --- | --- | --- |
| Workspace name | Yes | `workspace` | Name used to derive the user's Kubernetes resource names. |
| Triton development image | Yes | `nvcr.io/nvidia/tritonserver:26.06-py3` | Container image used for the workspace pod. |
| Image already has Development installed | No | disabled | Uses the `code-server` binary from the image instead of installing a standalone runtime during startup. Triton Control starts it on `0.0.0.0:8080`. |
| Workspace storage | Yes | `20Gi` | Persistent volume claim size mounted at `/workspace`. |
| VS Code theme | Yes | `Default Dark+` | Initial code-server color theme. |
| CPU request | No | unset | CPU request and limit for the workspace container. |
| Memory request | No | unset | Memory request and limit for the workspace container. |
| GPU count | No | unset | Adds an `nvidia.com/gpu` resource limit when greater than zero. |
| Registry credentials | No | unset | Docker `.dockerconfigjson` used to pull a private workspace image. |

In this field, **Development** refers to the code-server runtime. Enable
**Image already has Development installed** only if `code-server` is available
on the image's `PATH`. Triton Control overrides the image startup command and
starts that binary with `--bind-addr 0.0.0.0:8080`; the image does not need to
start code-server itself. The resulting workspace must listen on container port
`8080`, which is also used by the Kubernetes Service and health probes.
Otherwise workspace startup fails. When the option is disabled, the pod
installs a standalone code-server runtime under
`/tmp/triton-control-code-server` and starts it on the same port.

Creating a workspace provisions:

- one StatefulSet with a single workspace pod
- one internal Service on port `8080`
- one persistent volume claim mounted at `/workspace`
- Secrets for workspace authentication mode and optional registry credentials
- a ConfigMap containing the bundled **Triton Control Deploy** extension

The workspace is created in the Triton Control namespace when the backend runs
inside Kubernetes. For an external backend, the namespace is selected from
`TRITON_CONTROL_NAMESPACE`, `KUBERNETES_NAMESPACE`, or `POD_NAMESPACE`, with
`triton-control` as the fallback.

## Workspace Lifecycle

After creation, Triton Control polls the StatefulSet and pod until the workspace
status becomes `ready`. The page then embeds code-server through the
authenticated backend proxy:

```text
/api/development/<workspace-id>/proxy/?folder=/workspace
```

Both HTTP and WebSocket traffic pass through this proxy. The code-server
Service is therefore not exposed directly to the browser.

code-server webviews, including the full deployment form in **Triton Control
Deploy**, require a browser secure context. The native S3 Explorer does not use
a webview and works over HTTP as well as HTTPS. Use trusted HTTPS for
non-localhost hosts. Plain `http://triton-control.test` can load the
workspace, but plugin webviews may fail because browser crypto APIs are
unavailable. HTTPS with an untrusted certificate can still fail when
code-server registers its webview service worker. For local testing,
`http://localhost:<port>` via `kubectl port-forward` also works.

Use **Refresh** to read the latest pod status. Use **Delete** to remove the
managed StatefulSet, Service, Secrets, ConfigMap, and any legacy ingress.

!!! warning "Persistent volume retention"

    Deleting a Development workspace does not delete its Kubernetes PVC.
    Remove retained workspace claims separately when their data is no longer
    needed.

## Persistent Files and Extensions

The `/workspace` directory is persistent. Triton Control also keeps code-server
settings and user-installed extensions on that volume:

```text
/workspace/.triton-control/code-server-settings.json
/workspace/.triton-control/code-server-extensions/
```

These files survive pod restarts while the PVC remains available. New
workspaces receive the Python extension when its marketplace installation
succeeds and the bundled **Triton Control Deploy** extension.

Triton Control starts code-server with Workspace Trust disabled. The managed
`/workspace` folder is treated as the user's development area, so code-server
does not prompt users to mark the folder as trusted on each new workspace.

## S3 Profiles and S3 Browser

The bundled **Triton Control Deploy** extension displays saved S3 profiles as
folders beside `/workspace` in the native **Explorer**. Members and admins
create profiles through the Triton Control account menu's **S3 Profiles**.
Each profile stores its endpoint, bucket, optional prefix, region, access key,
encrypted secret key, addressing mode, and optional public CA certificate.
Profiles belong to the workspace owner.

### Connect, Switch, and Disconnect

1. Open **Explorer** in code-server.
2. Right-click a workspace file or folder and choose
   **S3 Operations → Choose Profile…**. Select your saved profile.
3. Expand **S3 · <profile name> · <bucket>** beside the workspace folders.

The connected context menu provides **Refresh**, **Disconnect**, and
**Switch Profile…**. Explorer's toolbar also exposes these connection actions.
Opening **S3 Operations** only opens the submenu; it does not prompt for a
profile. A connection needs no manually entered backend URL, login, credentials,
or connection tab. The S3 browser works over HTTP and HTTPS without a webview.

A profile prefix becomes its Explorer root. For example, a profile with prefix
`team-a` displays objects below `s3://<bucket>/team-a/`. Paths used in Argo
workflow manifests are still full bucket keys; include `team-a/` there.

### Browse and Create Buckets

On connection and **Refresh**, the plugin automatically checks whether the
profile can list buckets. If allowed, Explorer opens the endpoint's bucket view.
If denied or the check takes longer than **1.5 seconds**, it opens the configured
bucket instead, without an extra error popup. Checks for multiple profiles run
concurrently; ordinary file operations do not repeat this discovery check.
Prefix-scoped profiles are never probed. This timeout only bounds the optional
bucket-list check; an unreachable endpoint can still prevent bucket access.

Choosing **Show Profile Folder** remembers that view and disables automatic
bucket discovery for that profile until you choose **Show Buckets** again.


Right-click the connected S3 root and choose **S3 Operations → Show Buckets**.
Explorer shows **S3 · <profile name> · Buckets**, with the endpoint's buckets
beneath it. Expanding a bucket lists its objects; normal uploads, downloads,
editing, copy/move, and deletion work inside each bucket. Dragging between
buckets defaults to copying; dragging within one bucket defaults to moving.
Use **Show Profile Folder** to return to the saved bucket and prefix.

**S3 Operations → Create Bucket…** asks for a bucket name and uses the selected
profile's endpoint, credentials, CA, and region. Bucket creation is separate
from listing, so it does not require permission to list every bucket. Existing
bucket names are checked before creation; a failed existence check stops the
operation. Creation does not change the saved profile or grant new permissions.
Refresh an open bucket view after creation, or use **Show Buckets** to open it.

These actions are available only for profiles without a prefix. Prefix-scoped
profiles keep their existing boundary. The provider enforces permissions:
listing normally requires `s3:ListAllMyBuckets`, creation requires
`s3:CreateBucket`, and access to bucket contents requires its own permissions.
A listing failure leaves the current Explorer connection intact. Bucket names
returned by the provider are not a guarantee of read/write access.

The view uses the profile's configured endpoint and region for bucket access;
it does not discover or redirect to other regional endpoints. Use a profile
with the matching regional endpoint for buckets in another AWS region.
Only general-purpose S3 buckets are supported. Bucket deletion and renaming
are unavailable; use **Create Bucket…**, rather than Explorer's New Folder,
at the endpoint root.

### Transfer Files in Explorer

Drag files or folders directly onto an S3 bucket/profile root or folder, or
from S3 onto a workspace folder. Managed workspaces use these defaults:

| Drag | Default action |
| --- | --- |
| Workspace ↔ S3 | Copy |
| Between different S3 profiles or buckets | Copy |
| Within the same bucket and profile | Move |
| Ctrl-drag (Option on macOS) | Force copy |
| Shift-drag | Force move |

Workspace-only dragging and workspace-root reordering keep code-server's native
behavior. Right-button drop menus and Windows shortcut creation are not supported.

Folder transfers preserve the outer folder name. To create
`workflows/sklearn-iris-training/train_iris.py`, drop the local
`sklearn-iris-training` folder onto `workflows/`, or drop the file onto
`workflows/sklearn-iris-training/`.

Explorer supports opening/editing objects, creating folders, renaming, deleting,
and native Copy/Cut/Paste. The **S3 Operations** submenu additionally provides:

- **Copy** on a workspace or S3 item, followed by **Paste** on an S3 destination.
  These commands also work when browser clipboard access is unavailable.
- **Cut** on an S3 item, followed by **Paste** on an S3 destination.
- **Upload…** on an S3 destination, to choose workspace files or a folder.
- **Upload to Bucket Root…** on a workspace selection, to copy directly into
  the connected profile root, including its configured prefix.
- **Download…** on an S3 selection, to choose a workspace destination.

Explorer displays transfer progress and conflict prompts. The explicit upload
command provides replacement/skip choices and cancellation; files already
uploaded remain in S3. S3 moves copy the selected contents before deleting the
sources. Conditional requests protect against concurrent object changes.
Use **Refresh** after another client or an Argo workflow writes objects.

Transfers stream through temporary files in the workspace pod. Allow enough
pod disk space and keep operations within 5 GiB per object and 10,000 objects.
Opening an object directly in the editor is limited to 128 MiB; use transfers
for larger files. Multipart uploads are not implemented. Folder markers are
supported; the explicit workspace upload command omits empty directories and
rejects symbolic links. Downloads reject names that cannot safely become local
paths. S3 copies preserve content/user metadata, but not tags, ACLs, or version
history. Profile roots cannot be deleted or moved.

### Credentials and Certificates

The browser loads the owner's current profiles from the workspace-scoped
backend endpoint. S3 credentials are used in extension memory, not written to
workspace settings or `.aws/credentials`. The saved Explorer connection contains
profile identifiers and paths.

The workspace authenticates using `TRITON_CONTROL_PROFILE_TOKEN`, injected from
its Kubernetes Secret, and `TRITON_CONTROL_PROFILE_URL`. The token is accepted
only by the workspace profile endpoint. Ownership and account status are checked
on each request; concurrent lookups share only an in-flight request. Processes
running in the workspace can use this token to retrieve the owner's profiles.

HTTPS verification stays enabled. The selected profile's public CA certificate
is applied dynamically; the S3 browser does not require a shared
`minio-root-ca` ConfigMap. The S3 endpoint must be reachable from the workspace
pod. Save connection or certificate changes in **S3 Profiles**, then refresh.

### Existing Workspaces and Runtime Compatibility

New workspaces receive the plugin and startup integration automatically.
Existing workspaces need the updated extension VSIX, extension ConfigMap, and
startup command, including
`--enable-proposed-api triton-control.triton-control-deploy` for streaming
native transfers. After an update, restart the workspace and hard-refresh the
browser with **Ctrl+Shift+R**.

Windows-style S3 drag defaults use a managed code-server customization, tested
against **4.125.0**. Startup patches both browser bundles before serving the
workspace and rejects unsupported or incomplete bundles. Custom images that
already include code-server must provide that build and a writable workbench
bundle. See [Configuration](configuration.md) for the runtime version setting.

The separate deployment form still offers an **S3 profile** dropdown and
optional manual fields. Legacy `s3x.*` settings can supply defaults for those
manual deployment fields; they do not configure the native S3 browser. Neither
S3/R2 Explorer nor More Connect is required for the bundled browser.

## Create a Model Repository

The bundled **Triton Control Deploy** extension can create starter Triton model
repository structures in `/workspace`.

Open the Triton Control icon in the code-server Activity Bar and select
**New Model Repository**. The same command is also available from the command
palette and from the Explorer file or folder context menu. When launched from a file, the wizard
uses its containing folder as the parent for the new repository.

The command asks for:

1. repository type: **Single model** or **Ensemble pipeline**
2. repository target folder name
3. model name for a single model, or ensemble name plus step names for an
   ensemble
4. template/backend selection

The target folder becomes the Triton repository root. Model, ensemble, and step
names become folders inside that repository.

Single-model templates:

- Python backend
- ONNX Runtime
- TensorRT plan
- TensorRT-LLM
- vLLM
- PyTorch / LibTorch

Example single-model output:

```text
/workspace/my-repository/
  preprocess/
    config.pbtxt
    1/
      model.py
```

Artifact-based templates create the model folder, `config.pbtxt`, a version
folder, and editable README guidance explaining which model artifact must be
provided before deployment.

Generated `config.pbtxt` files are templates. Review and update the real model
name, backend/platform, input tensor names, output tensor names, shapes, and
data types before deploying. The scaffold defaults are intentionally generic.

Ensemble scaffolding creates child model folders plus a separate ensemble model
folder with `platform: "ensemble"` and editable `ensemble_scheduling` maps.
Preset pipelines are available for ONNX Runtime, TensorRT, TensorRT-LLM, and
vLLM models with Python preprocessing and postprocessing steps. All supported
model templates can also be selected in a custom ordered pipeline.

Example ensemble output:

```text
/workspace/fraud-pipeline/
  preprocess/
    config.pbtxt
    1/
      model.py
  score/
    config.pbtxt
    1/
      README.md
  postprocess/
    config.pbtxt
    1/
      model.py
  fraud_detector/
    config.pbtxt
```

After creation, the repository appears both in Explorer and in the Triton
Control Activity Bar view. The Triton Control view marks repositories as:

- **review model setup** when placeholder artifact guidance or generated
  `config.pbtxt` template values are still present
- **ready to deploy** when the scaffold placeholders are no longer detected

Select a repository row to switch to Explorer and reveal the real folder in the
workspace filesystem. Use the repository context menu to open the setup README
or `config.pbtxt` template. Review the model files and config before using the
deploy action. Repositories marked **ready to deploy** expose deploy actions in
the Triton Control view and context menu.

Repository discovery follows the filesystem. If a repository folder is deleted
from Explorer or the terminal, it is removed from the Triton Control view after
the filesystem watcher or the view refresh action runs.

## Deploy a Model Repository

The **Triton Control Deploy** extension has two modes:

- **Triton Control: Deploy Model Repository** opens the full webview form,
  uploads the repository to S3-compatible storage, and creates a self-deployed
  Triton instance.
- **Triton Control: Upload Model Repository (Simple Wizard)** uses native
  code-server prompts instead of a webview. Use it when Triton Control is
  opened through plain HTTP or an untrusted local certificate. It uploads the
  repository and prints the Add Deployment values in the output panel; finish
  the deployment from Triton Control's **Add Deployment** page.

The full webview mode requires trusted HTTPS or localhost because code-server
webviews use browser APIs and service workers that do not work on insecure
origins.

1. Create or edit a Triton model repository under `/workspace`.
2. Confirm required model artifacts are present and `config.pbtxt` matches the
   real model inputs and outputs.
3. Use a **ready to deploy** repository from the Triton Control Activity Bar
   view, or right-click the repository root or a single model folder in
   Explorer and run **Triton Control: Deploy Model Repository**.
4. Select an S3 profile, or expand manual S3 settings for a one-off deploy.
5. Confirm the Triton image, detected backend summary, S3 upload target, model
   control mode, and optional resources.
6. After full webview deployment, Triton Control opens the new instance and its
   deployment logs.

A repository should follow Triton's model layout:

```text
repository-root/
  model-name/
    config.pbtxt
    1/
      model.py
```

The extension reads the model name and backend from `config.pbtxt`. If no model
name is present, it prompts for one. If no backend is declared, the detected
backend summary shows `No backend in config.pbtxt`; otherwise it shows the
configured backend, for example `vLLM model backend`.

Selecting a single model folder uploads it below the chosen repository prefix
while keeping the deployment's `s3_url` at the parent repository prefix. The
form shows an **S3 upload target** preview before deploy. Manual S3 deployments
show the same final path as **Target path** at the end of the manual S3 section.

Model control is displayed as a summary and configured in the collapsed
**Model control** section. **Polling mode** shows the poll interval field;
**Explicit mode** hides it and uses the startup model behavior.

The resulting deployment behaves like one created through **Add Deployment**.
Its in-pod S3 repository connection is fixed at deployment time. Changing that
connection requires deleting and recreating the deployment.
