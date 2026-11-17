# Model preparation limits

Administrators configure model capture in **Settings → Model preparation**.

| Setting | Default |
| --- | ---: |
| Maximum individual file | 10 GiB |
| Maximum repository | 15 GiB |
| Maximum combined task inputs (model, dataset and baseline) | 15 GiB |
| Capture time limit | 3,600 seconds (1 hour) |
| Input download time limit | 3,600 seconds (1 hour) |

For a 20 GiB model, raise the file limit to at least 20 GiB, and raise the repository
and total-input limits to accommodate all supporting files and evaluation inputs.
For example, choose 24 / 32 / 40 GiB. Choose time limits appropriate to your storage
throughput. Byte hashing and downloads stream in 1 MiB chunks; the whole model is
not loaded into memory.

## Admin UI and Helm precedence

Helm supplies deployment defaults:

```yaml
modelPreparation:
  maxObjectGiB: 10
  maxRepositoryGiB: 15
  maxWorkspaceGiB: 15
  captureTimeoutSeconds: 3600
  downloadTimeoutSeconds: 3600
```

The chart maps these to `PREPARATION_MAX_OBJECT_GIB`,
`PREPARATION_MAX_REPOSITORY_GIB`, `PREPARATION_MAX_WORKSPACE_GIB`,
`PREPARATION_CAPTURE_TIMEOUT_SECONDS` and `PREPARATION_DOWNLOAD_TIMEOUT_SECONDS`.
These environment variables also work without Helm.

**Save limits** stores an administrator override in the database. All backend
replicas read it; no restart is required. **Reset to deployment defaults** removes
the override and restores the current Helm/environment values. A Helm upgrade does
not silently overwrite saved admin settings. The UI shows which source is active.

Only administrators can read, save or reset these settings through
`/api/settings/model-preparation` (GET, PUT, DELETE). Values must be whole numbers;
the file limit cannot exceed the repository limit, which cannot exceed the total
workspace input limit. Helm and backend validation reject invalid configuration.
Lowering limits can block larger existing task inputs; restore sufficient limits
to reopen or download them. Existing running workloads retain their mounted
manifest and volume sizing. Capture/download budgets are read when work starts;
retries use current settings.

## Large-model submission

**Submit model** starts a durable background capture and shows its status and
verified byte count. Save the page URL to reopen progress after leaving the page.
You can cancel unfinished captures or retry failed ones. Captures use a separate
worker so a long hash operation does not block deployment reconciliation.

API clients should use `POST /api/preparation/handoff-captures` with the existing
handoff request and an `Idempotency-Key`. The response is HTTP 202 with an operation
ID and status URL. Poll `/api/operations/{id}`; success returns `result.handoff_id`.
Read the finished receipt using `/api/preparation/handoffs/{handoff_id}`.
Cancellation and retry use the standard operation endpoints. The older synchronous
`POST /api/preparation/handoffs` remains compatible, but large captures should use
the asynchronous endpoint to avoid HTTP proxy timeouts.

The operation and receipt/task publication are fenced by a database lease.
Restarts recover queued work, and repeating a completed submission reuses its
receipt. Interrupted hashing starts again; partial hash state is not resumed.
Progress is for the current attempt. Source version, checksum, assignment and
release-validation rules remain in force.

## Storage capacity

Size limits do not provision physical disks. The backend verifies each downloaded
input in a temporary file before serving it to the task workspace. Provide free
backend temporary storage for the largest file (plus 64 MiB headroom), multiplied
by the number of concurrent downloads. Use `TMPDIR` on an appropriately sized
mounted disk if needed. Insufficient free space is rejected before download.

Task input volumes are sized from actual declared bytes plus 1 GiB overhead. The
init container requests that much ephemeral storage, so Kubernetes can schedule
it on a suitable node. When the default writable workspace PVC is too small for a
copy of the inputs, a new task workspace automatically requests a larger PVC.
Explicitly requested PVC sizes remain the operator's choice. Existing PVCs are
not resized by changing the preparation limits.

The init container receives the configured download timeout and exact total byte
budget. There is no separate fixed 3 GiB input limit or 4 GiB volume cap. Use the
internal backend service for `DEVELOPMENT_PROFILE_API_URL`; an external proxy on
that path must permit the configured download duration.

## Upgrade and verification

Startup creates the `preparation_config` singleton table through the existing
schema initialization lock. No existing receipts or model bytes are rewritten.

Tests cover defaults, admin precedence/reset, validation, streaming admission for
a simulated 20 GiB input after an override, dynamic staging disk requests, durable
capture, cancellation and retries. The 20 GiB unit test uses a repeated buffer and
a stubbed hash to exercise size/streaming boundaries; it is not a 20 GiB throughput
benchmark. PostgreSQL tests cover operation/receipt transactions and ownership.
