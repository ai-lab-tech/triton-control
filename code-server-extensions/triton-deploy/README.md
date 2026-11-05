# Triton Control Deploy Extension

This code-server extension uploads a selected Triton model folder or Triton
model repository root to S3-compatible storage. The full webview flow uploads
and then calls Triton Control's existing `POST /api/deployments` endpoint. The
Simple Wizard is upload-only and prints the values needed to finish deployment
from Triton Control's **Add Deployment** page.

## Flow

### Build from a downloaded Hugging Face checkpoint

<!-- hf-builder-version:start -->
Tested builder version: `0.1.2`.
<!-- hf-builder-version:end -->

Enable **Install HF Triton Builder** when creating a workspace to use the builder
in the wizard, Python code and the **Python (Triton Control)** notebook kernel.

Manual installation (Python 3.10+):

```bash
python3 -m pip install triton-hf-builder
```

For a custom Python interpreter, set `tritonControlDeploy.hfBuilderPython`.

1. Select **Build from Hugging Face** in the Triton Control workspace view or
   **Triton Control: Build Repository from Hugging Face** in the command palette.
2. Select the already downloaded HF model directory.
3. Choose runtime, device, versioned serving image, model name and GPU count.
   The task is inferred automatically from checkpoint metadata. A task dialog
   appears only when inference is ambiguous; detected tasks are shown in the output.
4. Enter a new repository folder, using an absolute path or a path relative to the workspace.
5. Review the generated `requirements.txt`, `serving.json` and model config.
   The final action can open the config or start the existing deployment form.

To override a detected task, use **Triton Control: Build Repository from Hugging Face
(Choose Task)** from the command palette, for example to select `feature-extraction`.
The normal command leaves the optional Python `task` argument unset when metadata
is clear. A runtime incompatible with the detected task produces an error instead
of changing the task automatically.

The Python core supports Transformers text tasks, native vLLM, and TensorRT-LLM's
public LLM API. It does metadata checks without loading weights or using a GPU.
Actual serving compatibility still needs inference validation in the target image.
The editor contains only the wizard and subprocess integration; generator and
adapter logic live in https://github.com/ai-lab-tech/triton-hf-builder.

When deploying a generated repository root, the form reads the image, GPU count
and requirements from its serving metadata. These remain editable. Triton Control
already installs `requirements_txt` before starting Triton, so no Dockerfile is
needed for this workflow. Resolve/pin the dependencies against the target image;
the generated requirements are starter specifications rather than a tested lock.

### Create a model repository

1. Open a workspace folder in code-server.
2. Open the Triton Control Activity Bar view and select **New Model
   Repository**. The command is also available from the command palette and
   from the Explorer file or folder context menu. When launched from a file,
   the wizard uses its containing folder as the parent for the new repository.
3. Choose `Single model` or `Ensemble pipeline`.
4. Enter the repository target folder name.
5. For a single model, enter the Triton model name and choose a template.
6. For an ensemble, enter the ensemble model name and choose a preset or custom
   ordered pipeline. Step names become child Triton model folders.

The target folder is the Triton repository root. The model, ensemble, and step
names become folders inside that repository.

Supported single-model templates:

- Python backend
- ONNX Runtime
- TensorRT plan
- TensorRT-LLM
- vLLM
- PyTorch / LibTorch

Python scaffolds include a starter `model.py`. Artifact-based templates include
editable README guidance in the version folder explaining which model artifact
the user must provide before deployment.

Generated `config.pbtxt` files are templates. Review and update the model name,
backend/platform, tensor names, shapes, and data types before deploying a real
model.

Preset ensembles:

- Python -> ONNX Runtime -> Python
- Python -> TensorRT -> Python
- Python -> TensorRT-LLM -> Python
- Python -> vLLM -> Python

Custom ensembles can use templates marked as ensemble-step eligible. The
generated ensemble `config.pbtxt` contains `platform: "ensemble"` and editable
`ensemble_scheduling` input/output maps.

### Workspace view

The Triton Control Activity Bar view lists workspace actions and discovered
Triton model repositories below the workspace root.

- **New Model Repository** starts the scaffold wizard.
- Selecting a repository switches to Explorer and reveals the real folder in
  the workspace filesystem.
- Repositories marked **review model setup** still contain placeholder artifact
  guidance or generated `config.pbtxt` template values. Use the repository
  context menu to open setup guidance before deploying.
- Repositories marked **ready to deploy** expose deploy actions in the view and
  context menu.
Repository discovery follows the filesystem. If a repository folder is deleted
from Explorer or the terminal, it is removed from the Triton Control view after
the filesystem watcher or the view refresh action runs.

### Deploy a model repository

1. Confirm the model files are present and `config.pbtxt` matches the real
   model inputs and outputs.
2. Use a **ready to deploy** repository from the Triton Control view, or
   right-click a Triton model folder or model repository root in Explorer and
   run `Triton Control: Deploy Model Repository`.
3. The extension detects the model name plus `backend` or `platform` value from
   `config.pbtxt`. If no model name is found, it asks for one. When neither
   backend nor platform is declared, the form shows
   `No backend or platform in config.pbtxt`.
4. Select a saved S3 profile. The **Repository prefix**
   is an optional parent path; the upload target preview shows the final
   `s3://...` path before deploy. Expand **External access** to choose cluster-only access or an Ingress
   hostname, HTTP/HTTPS, and optional basic authentication. External access and
   HTTP are available only when permitted by the administrator. Controller class,
   routing and certificates are administrator-managed.
5. The extension uploads files below `bucket/prefix/model-repository-path`.
6. The webview calls `/api/deployments` with the current Triton Control browser
   session, so the normal Add Deployment path is reused.

The full deploy form is a code-server webview. Browser webviews require trusted
HTTPS or localhost. If Triton Control is opened through plain HTTP or an
untrusted certificate, run `Triton Control: Upload Model Repository (Simple
Wizard)` instead. The simple wizard uses native VS Code prompts, uploads the
repository to S3, and prints the Add Deployment values in the output panel.

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

## S3 Browser and Workspace Drag-and-Drop

Saved S3 profiles appear as folders alongside your workspace in **Explorer**.
Use the Explorer toolbar’s plug (**Choose Profile…**), disconnect, and refresh
buttons. These actions are also available under **S3 Operations** when right-clicking an S3 folder;
right-click a workspace folder and choose **S3 Operations → Choose Profile…** to connect when disconnected.
When connected, both workspace and S3 context menus show **Refresh**,
**Disconnect**, and **Switch Profile…**. Opening the submenu never selects a profile.
Use **Choose Profile…** to select one, or **Refresh** to load all
profiles. No URL, login, token, or webview is required. Credentials and CA
certificates come dynamically from the workspace owner's saved profiles.

On connection and **Refresh**, the plugin automatically checks whether the
profile can list buckets. If allowed, Explorer opens the endpoint's bucket view.
If denied or the check takes longer than **1.5 seconds**, it opens the configured
bucket instead, without an extra error popup. Checks for multiple profiles run
concurrently; ordinary file operations do not repeat this discovery check.
Prefix-scoped profiles are never probed. This timeout only bounds the optional
bucket-list check; an unreachable endpoint can still prevent bucket access.

Choosing **Show Profile Folder** remembers that view and disables automatic
bucket discovery for that profile until you choose **Show Buckets** again.

Use **S3 Operations → Show Buckets** on a connected root to browse buckets at
its endpoint, and **Show Profile Folder** to return to the saved bucket.
**Create Bucket…** creates a general-purpose bucket in the profile's region
when the credentials permit it. Existing bucket names are checked first.
These actions require a profile without a prefix; scoped profiles retain their
boundary. Listing requires `s3:ListAllMyBuckets`; creation requires
`s3:CreateBucket` and a successful existence check. Bucket contents have
separate permissions. Cross-bucket drags default to copying. Bucket deletion
and renaming are not supported, and regional endpoints are not auto-discovered.

Use normal Explorer controls for files and folders:

- Drag between workspace and S3, or between S3 folders/profiles. Files can be
  dropped directly onto the bucket/profile root.
- Right-click any workspace file or folder → **S3 Operations → Copy**, then
  right-click the bucket or destination folder → **S3 Operations → Paste**.
  **Upload to Bucket Root…** copies the selection directly to the connected
  profile root (its configured prefix, if any), without an extra folder.
- Use Copy, Cut, and Paste to copy or move selections. The **S3 Operations → Copy**,
  **Cut**, and **Paste** menu actions also work when the browser
  blocks system clipboard access (for example, an HTTP origin).
- Rename, create folders, or Delete using Explorer's context menu.
- Use **S3 Operations → Download…** to select a workspace destination, or
  **S3 Operations → Upload…** to select local sources.

Managed code-server uses Windows Explorer drag defaults for S3 transfers:

- Workspace ↔ S3, or between different S3 profiles: **copy**.
- Within one S3 profile: **move**.
- **Ctrl-drag** forces copy (Option on macOS); **Shift-drag** forces move.
- Drop on a bucket root or folder. Explorer displays overwrite/move prompts.

The cursor and committed action use the same rule. Workspace-only dragging and
workspace-root reordering retain code-server's native behavior. Right-button
"drop action" menus and creating Windows shortcuts are not implemented.

This behavior requires the managed startup customization in
`code-server-s3-dnd.js`, tested against the pinned code-server **4.125.0** bundle.
It is applied before code-server starts, and is idempotent. A different or
partially patched Explorer bundle is rejected before serving the workspace,
rather than silently reverting to move-by-default. Custom images containing
code-server must use that build and allow writing its workbench bundle.
Existing workspaces need the updated startup command and ConfigMap. Reload
browser windows after applying the customization.

Explorer handles transfer progress and conflict prompts.
Moving within S3 copies all selected contents successfully before deleting the
originals. Conditional requests protect against concurrent object changes.
S3 folder operations include nested objects and folder markers. Profile roots
cannot be deleted or moved; previous object versions are not purged.

Transfers stream through private temporary files in the code-server pod and
support up to 5 GiB per object and 10,000 objects per operation. Available pod
disk space must accommodate the transfer. S3-to-S3 copies preserve HTTP content
metadata and user metadata, but do not copy object tags, ACLs, or version history.
Refresh Explorer to see changes made by other clients. Symbolic links and
ambiguous or unsafe filesystem names are rejected by the upload/download commands.
Multipart transfers are not implemented.

The profile endpoint must be reachable from the pod. HTTPS verifies certificates
using the selected profile's CA bundle. Profiles need `s3:ListBucket`,
`s3:GetObject`, and `s3:PutObject`; moves and deletion also need `s3:DeleteObject`.
Credentials are never stored in workspace settings or extension storage.

Managed workspaces enable the extension's `fsChunks` API for streaming native
Explorer transfers. Existing workspaces need the updated extension and
`--enable-proposed-api triton-control.triton-control-deploy` code-server startup
flag, followed by a restart. The native connection also works over HTTP.

## S3 Profiles

Members and admins can manage reusable S3 profiles in Triton Control from the
account menu. The extension loads those profiles from `/api/s3-profiles` and
shows them in the **S3 profile** dropdown.

S3 profiles are private to the signed-in user. Other users, including
administrators, cannot list, edit, or delete a profile through the S3 profile
API. Each user must create their own profile, even when multiple users connect
to the same object store.

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

The deployment form requires a saved profile. Manage connection credentials and
optional CA certificates in Triton Control's **S3 Profiles** menu. Set the
repository prefix in the form and check **S3 upload target** before deploying.

The extension fetches current credentials through the managed workspace endpoint
when uploading. For MinIO and other custom endpoints, configure path-style
addressing in the profile. An HTTPS S3 CA certificate is passed to the Triton pod
so it can trust the object store.

## Detected Backend and Model Control

The deploy form shows detected backend/platform and model control as summary
values, not editable inputs. Backend/platform is read from `config.pbtxt`:

- `backend: "vllm"` is shown as `vLLM model backend`.
- `platform: "pytorch_libtorch"` is shown as `PyTorch/LibTorch platform`.
- Other backend values are shown as `<backend> model backend`.
- Missing backend and platform are shown as
  `No backend or platform in config.pbtxt`.

Deployment-specific fields such as image, extra Python packages, resources, and
model control are not reused from previous deployments. S3 connection settings
can still be reused.

Model control uses **Polling mode** by default. **Poll interval seconds** is
shown only in polling mode. Switch to **Explicit mode** when the deployment
should load only the configured startup model.

## Resources

The deploy form has a collapsed **Resources** section. The default values shown
there are real form values and are sent with the deployment:

- CPU: `2`
- RAM: `4` with a fixed `Gi` suffix
- GPU count: `0`

RAM accepts only non-negative whole Gi values. A positive value such as `4` is
sent as both the Kubernetes memory request and limit (`4Gi`). Enter `0` or
clear the field to omit the memory setting. Change or clear the resource values
before deploy if the model needs different resources. Values present in the
form are sent to Triton Control for every backend.

## Package

From this directory:

```powershell
npm install
npm run package
```

Install the generated `.vsix` in code-server.

### Endpoint policy

The deployment form reads `GET /api/deployments/policy`. When
`tritonDeployments.ingressEnabled=false`, only cluster-only deployments are
available. HTTP additionally requires administrator permission; basic
authentication requires HTTPS and both username and password. Passwords are
sent only with the deployment request, not saved in extension settings.

The backend rechecks policy and hostname conflicts when processing
`POST /api/deployments`. A failed policy lookup leaves external access disabled.
For existing deployments, use Triton Control's **Endpoints** tab.

Hostname fields accept only a DNS hostname, e.g. `smollm`, without
`http://`, `https://`, a port or a path. Choose the protocol separately
(`ingress_scheme` in REST/MLflow configuration). URLs previously supplied in
`ingress_host` must be split into these two fields; existing deployed endpoints
are unchanged.


The deployment form uses saved S3 profiles only. Manual S3 connection fields and
profile creation have been removed from this form. Manage profiles in Triton
Control. The workspace resolves the selected profile again before uploading, so
current credentials are used and are not saved to extension settings by this form.
