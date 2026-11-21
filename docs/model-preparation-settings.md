# Model preparation limits

Administrators configure model capture in **Settings → Model preparation**.

| Setting | Default |
| --- | ---: |
| Maximum total task inputs (model, dataset and baseline) | 15 GiB |
| Maximum writable workspace storage | 50 GiB |
| Shared transfer timeout | 3,600 seconds (1 hour) |
| Require S3 versioning for handoff inputs | On |

The total input ceiling applies to all files combined. There are no separate
per-file or repository size settings. For a 20 GiB model plus 4 GiB of evaluation
inputs, allow at least 24 GiB of total inputs; automatic writable storage is 36 GiB.
The shared timeout applies to the capture/verification job and each file download.
Transfers stream in 1 MiB chunks; the whole model is not loaded into memory.

## Admin UI and Helm precedence

Helm supplies deployment defaults:

```yaml
modelPreparation:
  maxInputGiB: 15
  maxWorkspaceStorageGiB: 50
  transferTimeoutSeconds: 3600
```

The chart maps these to `PREPARATION_MAX_INPUT_GIB`,
`PREPARATION_MAX_WORKSPACE_STORAGE_GIB`, and `PREPARATION_TRANSFER_TIMEOUT_SECONDS`.
These three environment variables also work without Helm.

**Save settings** stores an administrator override in the database. All backend
replicas read it; no restart is required. **Reset to deployment defaults** removes
the override and restores the current Helm/environment values. A Helm upgrade does
not silently overwrite saved admin settings. The UI shows which source is active.

Turn off **Require S3 versioning for handoff inputs** to allow submissions from
unversioned S3. The form warns users to keep their files available. Workspaces
verify the original checksums before staging files. If a file changes or
disappears, submit a new handoff revision. Resetting the setting requires
versioning for new captures. Existing handoffs retain their original checksums
and version IDs where available.

Only administrators can read, save or reset these settings through
`/api/settings/model-preparation` (GET, PUT, DELETE). Values must be whole numbers;
the writable storage maximum must be at least 6 GiB. Helm and backend validation
reject invalid configuration.
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
it on a suitable node. Writable workspace storage is calculated separately:

```text
inputs = model repository + evaluation dataset + baseline (actual captured bytes)
working space = max(5 GiB, inputs × 50%)
writable storage = ceil(inputs + working space), in GiB
```

For example, 16 GiB of inputs requires 24 GiB of writable storage with the defaults.
The combined-input admission limit must also permit those 16 GiB. These global
admission limits remain safety ceilings, not per-task disk allocations.

Click **Open task workspace** on the receipt to preview the actual input size,
recommended writable storage, and administrator maximum. Choose more space for
conversion/build output if needed, then click **Create workspace**. Both the API
and UI reject sizes below the recommendation or above the maximum. If the
recommendation exceeds the maximum, creation is blocked rather than silently
allocating too little storage. The 50 GiB maximum is not allocated to every task.

API clients can preview using `GET /api/preparation/tasks/{task_id}/workspace-sizing`.
Omitting `workspace.storage_size` on creation uses automatic sizing; an explicit
size is checked against the same policy. The preview and creation both require
task assignment and input access. Creation rechecks the current policy.

Reopening reuses the stored allocation, even if the sizing policy changes. Existing
PVCs are not resized automatically. Read-only verified inputs remain on a separate
volume, so total node storage must cover both input staging and writable storage.

The init container receives the configured download timeout and exact total byte
budget. There is no separate fixed 3 GiB input limit or 4 GiB volume cap. Use the
internal backend service for `DEVELOPMENT_PROFILE_API_URL`; an external proxy on
that path must permit the configured download duration.

## Upgrade and verification

Replace the earlier `modelPreparation` keys with the three keys above when
upgrading this feature branch; retired Helm keys are rejected by the schema.
Existing saved admin overrides retain their combined-input ceiling and storage
maximum. The smaller of their old capture/download timeouts becomes the shared
timeout. Separate file/repository ceilings and configurable working-space values
are retired. Saving again writes only the three current settings.

Startup creates the `preparation_config` singleton table through the existing
schema initialization lock. No existing receipts or model bytes are rewritten.

Tests cover defaults, admin precedence/reset, validation, streaming admission for
a simulated 20 GiB input after an override, dynamic staging disk requests, durable
capture, cancellation and retries. The 20 GiB unit test uses a repeated buffer and
a stubbed hash to exercise size/streaming boundaries; it is not a 20 GiB throughput
benchmark. PostgreSQL tests cover operation/receipt transactions and ownership.
