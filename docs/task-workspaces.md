# Task workspaces

An assigned engineer can open a workspace from a scientist handoff with a verified model source. The
handoff page offers **Claim task**, then **Open task workspace**. The workspace
explorer contains **Verified inputs (read-only)** and **Conversion (writable)**.
Each engineer can have one personal workspace alongside their task workspaces.
If you opened a task workspace first, choose **Create personal workspace** in its toolbar.

When you have several workspaces, select one under **Development → Workspaces**
in the navigation. Task workspaces use the task title. To remove a finished
workspace, open it and select **Delete** in its toolbar. The handoff and task
history remain available. After you release a claim or the task is cancelled,
your workspace is listed by ID with the status **Cleanup only**. Select it to delete it.

When creating the task workspace, choose its image, GPU count, optional CPU
cores and RAM, and writable storage. The default GPU count is zero. Leaving
CPU or RAM blank uses the cluster defaults. Reopening displays the saved
configuration and uses it again.

## HTTP interface and ownership

`POST /api/preparation/tasks/{task_id}/workspace` accepts:

```json
{
  "workspace": {
    "image": "nvcr.io/nvidia/tritonserver:26.06-py3",
    "gpu_count": 1,
    "cpu": "1500m",
    "memory": "4096Mi",
    "storage_size": "20Gi",
    "install_hf_builder": true,
    "install_mlflow": true
  }
}
```

An empty body uses defaults for a new workspace and the saved settings when
reopening a workspace. The response contains
`task_id` and the existing `CodeServerDTO`, including its authenticated proxy URL.
`GET /api/preparation/tasks/{task_id}/workspace` refreshes the workspace status.
The status is `creating` while the init container verifies files, `blocked` on
staging failures, and `ready` when the editor becomes ready. These are workspace
states, not validation results or release approval.

Only the currently assigned eligible engineer may open the workspace. An active
administrator may explicitly supply `admin_recovery_reason` to open their own
recovery workspace; the reason and actor are recorded in task history. Being an
administrator or another team member alone does not open another user's editor.
If an existing workspace belongs to another user, an administrator must delete
it through the existing workspace deletion endpoint before creating a replacement.
Deletion is audited and works even after cancellation or membership revocation.
A replacement uses a new workload/PVC name, so it cannot inherit the previous
owner's conversion files. Existing PVC retention follows the workspace policy.

Current account, role, assignment, team eligibility and source profile identity
are checked on reads, browser proxy requests and workspace credential endpoints.
Long-lived editor WebSockets are rechecked every 15 seconds. This revokes future
access; it cannot retract files already read or credentials already exported by
existing profile APIs. S3 profiles currently follow the installation-wide
member/admin permission model. Team membership adds no artifact permission.

## Verified inputs and retries

Repository-folder handoffs preserve every captured path under
`/inputs/verified/source/` (for example `iris/1/model.onnx`). Every file is
downloaded by its recorded S3 version and verified independently. No ZIP creation
or extraction is needed. Supporting files and empty files are preserved. The
source inventory hash covers the complete repository. See the
[repository submission walkthrough](scientist-handoffs.md#submit-an-unpacked-triton-repository).


A database task lock and unique association serialize concurrent opens. The
association, selected configuration, immutable inputs and audit event commit
before Kubernetes resource creation. Repeated requests reuse the same workspace;
changed settings or inputs return 409. An interrupted apply remains retryable
using the saved identity. Retry re-applies the same resources and preserves
existing workspace tokens. A completed resource apply is recorded independently of
pod readiness, so status polling cannot hide an interrupted apply. Kubernetes also retries failed init containers.

A separate administrator-controlled Python init image retrieves only the exact
captured S3 versions through a token-authenticated backend endpoint. The backend
checks the current profile endpoint, bucket and prefix, downloads to a private
temporary file and checks size, version and SHA-256 before serving bytes. The
init container verifies the streamed bytes again. No archives are extracted.
Input size and download budgets follow [Model preparation settings](model-preparation-settings.md).
Verified inputs use a pod-local Kubernetes `emptyDir` volume sized from the captured
bytes plus overhead. When Kubernetes replaces the pod, the init container stages
and verifies the inputs again in a fresh `emptyDir`.

All files must pass before the init container atomically publishes
`/inputs/verified`. Kubernetes mounts `/inputs` read-only in the editor, which
has all Linux capabilities dropped and privilege escalation disabled. The editor
has no Kubernetes service-account token. Conversion files live separately in
`/workspace/conversion` on the writable volume backed by the workspace's persistent
volume claim (PVC). The writable storage size selected during workspace creation
applies to this PVC. Conversion files persist when a replacement pod mounts the
same claim. Conversion is the first editor folder so builder and scaffolding
defaults target writable storage.

`/inputs/verified/task.json` contains the task ID, exact snapshot, file paths,
version IDs, hashes, expected behavior and acceptance criteria. It contains no
S3 credentials, source profile selectors or presigned URLs. Criteria are supplied
by the user; users should not put credentials into those text fields.

The init image defaults to `python:3.12-alpine`; administrators can set
`TASK_WORKSPACE_STAGING_IMAGE` to their approved Python image, including a pinned
registry digest. The editor image never supplies the verifier. Configure this
through Helm `app.env` or the deployment's backend environment. `DEVELOPMENT_PROFILE_API_URL` must
be reachable from workspace pods, as with the existing workspace integrations.
Registry and S3 configuration remain environment-local. No production credentials
are copied from a handoff, and inline registry credentials are rejected.

If a source is missing, changed, unauthorized, or corrupt, the editor does not
start. Correct the storage/profile problem and reopen the same task workspace.
Changed source content requires a new immutable handoff/snapshot revision.

## Snapshot integration and upgrades

`WorkspaceInputs` is the common input contract. Existing handoff receipts adapt
to it without rewriting their history. Future authorized experiment capture can
call `repositories.task_workspaces.register_snapshot_inputs` inside its own
transaction, using the task's `source_snapshot_id`. The captured source inventory
must match the snapshot digest. Registration is immutable and does not itself
grant access. This implements the consumption interface, not experiment capture.

Startup adds `task_workspaces` and `workspace_snapshot_inputs` under the existing
PostgreSQL schema advisory lock. Unique constraints enforce one active workspace
per task and one task per workspace. Snapshot input history is append-only.
Existing workspace tables and handoff records need no data rewrite.

## Verification

```sh
python -m unittest tests.test_task_workspaces tests.test_development -v
PREPARATION_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_task_workspaces_postgres -v
TASK_WORKSPACE_TEST_KUBERNETES=1 KUBERNETES_KUBECONFIG_PATH=/path/to/config python -m unittest tests.test_task_workspaces_kubernetes -v
```

The PostgreSQL suite exercises concurrent opens, interrupted provisioning,
authorization and repeatable concurrent upgrades. The Kubernetes suite creates
unique temporary namespaces and tests corrupt-input startup blocking, actual
read-only mount enforcement, a writable conversion directory and pod recreation.
It uses a small HTTP fixture, not a real Triton inference workload. CI runs both
suites in the existing PostgreSQL/Kubernetes job.

### Automatic writable storage

On the handoff receipt, **Open task workspace** shows the actual input bytes and
recommended disk size before creation. The recommendation includes a working copy
of the inputs plus automatic space for conversion and outputs. Engineers can
request more space within the administrator maximum. See
[model preparation settings](model-preparation-settings.md#storage-capacity) for
the sizing formula, defaults, and Helm configuration. Existing workspaces retain
their original allocation when reopened.

## Engineering queue

Open **Development → Engineering queue** to inspect visible preparation tasks, their source revision,
team and assignee. Task detail supports revision-checked claim/release and information
requests; a conflicting claim reloads the current assignment. Missing inputs remain
visible and do not prevent claiming a task. Snapshot tasks can open a workspace while
retaining their recorded gaps. Build attempts, preparation attempts and retained
candidates are displayed separately from task state and imply no production approval.

On a development serving instance, **Prepare for production** opens the engineering
queue's **Submit model** form. It selects a matching S3 profile by endpoint, bucket
and repository prefix, and preselects the version when only one is ready. Review
the version and evaluation details before submitting. The handoff captures the
selected version and its shared model configuration under the installation's S3
versioning policy. **Open engineering task** opens the original task for a
deployment created from its workspace.

### Save and restore converted models

In the assigned workspace, right-click a Triton model directory under
`/workspace/conversion` and choose **Save Converted Model to S3**. Choose a
saved profile with a versioned bucket. Each export has its own prefix and
preserves the model configuration and version directories. Task history shows
the export status, S3 location, and verified object versions. A failed upload
can leave partial objects under its own prefix. Retry the command to create a
new export. Only verified receipts are available for restoration.

After opening a replacement workspace for the same task, use **Triton Control:
Restore Saved Conversion** from the command palette. It downloads the exact
recorded versions, verifies their checksums and writes to a new directory.
The current profile must still authorize access to the original destination.

### Reclaim retired task workspace volumes

Run `python -m app.services.development.claim_retention` in the development
backend environment to see eligibility and retention reasons. Use `--apply` to
request deletion of eligible claims. Set `TASK_WORKSPACE_RETENTION_DAYS` to
control the retention interval after retirement (default 30 days). Helm supplies
the installation identity. For other installations, configure a stable
`TRITON_INSTALLATION_ID` before creating workspaces.

The operator command checks the task history, exact S3 versions, Kubernetes
attachments and files on the retired volume using a read-only inspection pod.
Additional files, including personal workspace files outside the conversion
directory, retain the claim. The task records deletion requests. Subsequent
runs report the bound PV and reclaim policy where the operator has Kubernetes
permission to get persistent volumes. A storage class with the Retain policy
can leave a released PV for the storage administrator.

Enable `development.workspaceRetention.enabled` in Helm to schedule this
operator command. The default schedule is 02:17 daily, with a 30-day retention
period. Configure `development.workspaceRetention.schedule` and
`development.workspaceRetention.days` to override them.

Each current model directory must match a single verified export, including its
complete file list. Scheduled cleanup requires Kubernetes 1.30 or later with
ValidatingAdmissionPolicy enabled. The chart installs a namespace-scoped
admission rule reserving each task claim for its designated StatefulSet pod
name. During inspection a read-only pod holds that name with a finalizer until
the deletion request completes. Other pod names cannot mount the claim.

The admission resources are retained when disabling the schedule or removing
the chart. Remove them only after all retention processes have stopped. If an
operator process crashes, a remaining inspection pod protects the claim. Stop
all retention processes before manually removing its
`triton-control.io/inspection` finalizer and deleting the inspection pod.
Restart cleanup to perform a fresh inventory.
