# Immutable development experiment snapshots

Experiment snapshot capture copies an authorized development experiment into controlled
staging and
creates one preparation task referencing the immutable snapshot. It does not
change, restart, scale, pause or delete the development workload. It does not
create a production release, approve it or claim validation success.

## Configure controlled staging

The development Helm chart mounts a persistent snapshot volume by default and
uses its namespace/release installation ID. The claim requests 20 GiB with
ReadWriteOnce access. Configure `modelPreparation.snapshotVolume` for the desired
size, storage class and access mode. Use ReadWriteMany when backend replicas run
on different nodes. Helm retains the claim on uninstall.

**Require S3 versioning for handoff inputs** in Admin settings applies to the
original serving model files. When cleared, capture reads unversioned files with
their ETags, hashes their contents, rechecks the source, and retains a separate
copy for the task. Subsequent changes to the serving files do not change that copy.

For an explicit store, configure one of these options. Outside Helm, also set a
stable `TRITON_INSTALLATION_ID` on every backend replica.

- `TRITON_PREPARATION_SNAPSHOT_PROFILE_ID`: existing global S3 profile ID. Its
  bucket must have versioning enabled. Optional
  `TRITON_PREPARATION_SNAPSHOT_PREFIX` defaults to `preparation-snapshots` beneath
  the profile prefix. Each publication uses new keys and retains actual object
  version IDs; callers cannot choose this destination in the request.
- `TRITON_PREPARATION_SNAPSHOT_ROOT`: absolute persistent private directory shared
  by all backend replicas. Publications are atomically finalized, verified and
  made read-only. An ephemeral container directory is unsuitable for retained
  snapshots.

An explicit root or profile in Helm `app.env` overrides the default volume.
Set `modelPreparation.snapshotVolume.enabled=false` when supplying storage
configuration through `app.envFrom`. Source and destination prefixes may not overlap the selected
models. Preserve destination configuration and retained object versions across
upgrades; changing a provider location requires an explicit data migration.
Do not apply lifecycle deletion to referenced snapshots. This ticket adds no
retention garbage collector. Failed/cancelled publication can leave unreferenced
staging objects; they are never exposed as completed snapshots/tasks. Operator
cleanup must verify database references before removing them.

`TRITON_CONTROL_MODE` defaults to `development`; capture is rejected in production
or unknown modes. Retained snapshot/task reads remain subject to current account
and task authorization.

## Request capture

The serving page opens the engineering queue's prefilled **Submit model** form.
The following API is available to clients that explicitly request an experiment
snapshot. Existing snapshot operation links can still show their status.

`POST /api/instances/{instance_id}/preparation-requests` requires an interactive,
currently active member/admin, instance access, and `Idempotency-Key` (1–128
characters). Engineering-team membership alone grants no instance or workspace
access. Current database roles are rechecked; stale admin claims do not bypass
access. Delegated workflow/workspace deployment tokens cannot capture experiments.

For managed Kubernetes deployments, first obtain deployment UID and generation
from `GET /api/instances/{id}/pods`. Example:

```json
{
  "title": "Prepare the tested Iris experiment",
  "deployment_uid": "<UID from pod state>",
  "expected_generation": 2,
  "models": [{"name": "iris_classifier", "versions": ["1"]}],
  "workspace": {
    "workspace_id": 7,
    "files": ["iris-project/Dockerfile", "iris-project/requirements.txt", "iris-project/model.py"]
  },
  "runtime_changes": "unknown",
  "expected_behavior": "Return the expected class for each reviewed fixture.",
  "acceptance_criteria": "Agree the dataset and thresholds with engineering."
}
```

Replace the UID, generation, workspace ID and paths with actual authorized values;
the placeholder UID above is explanatory and is not valid request data. Workspace
selection is optional. Selected files are relative to `/workspace`, must exist,
and are captured regardless of Git status. No Git commit or clean checkout is
required. You can optionally supply `evaluation_files` (path/size/SHA-256 entries);
the service checks that these refer to bytes actually included in the model or
workspace inventory. Declarations do not constitute passed evaluation evidence.

Capture requires the instance's recorded S3 repository. Requests that supply a
different repository or select an unserved version are rejected. External endpoints
also require a configured S3 source. Their runtime
image is unobserved. Optional `supplied_runtime_image` is a
pinned user-supplied reference, retained separately from observed runtime
identity and never represented as the actually tested image.

The response is HTTP 202 with `operation`, `snapshot_url` and a `Location` header
pointing to `/api/operations/{id}`. Progress, cancellation and failed-operation
retry use the existing operation API. The completed result contains snapshot ID,
task ID and gaps. `GET /api/preparation/snapshots/{id}` returns the immutable
manifest, its digest, retained artifact references and the current task state.
The submitter and authorized task readers can inspect it without requiring the
original development deployment to still exist.

## Capture source provenance

Add a `source` object to the capture request to describe how the experiment
was prepared. The source selector works without an MLflow or HF builder installation.

| `source.kind` | Fields to supply when available |
| --- | --- |
| `hf` | `repository` (for example `owner/model`), `revision`, `resolved_commit`, `serving_manifest`, and `checkpoint_files`. |
| `example` | `repository` (for example `github.com/owner/repo`), the checkout's full `commit`, and `project_path` in that checkout. Use `.` for the repository root. |
| `manual` | Select the local project files via `workspace.files`. Use `author` for supplied attribution and `ai_assisted` for optional authoring context. The receipt also records the authenticated submitter. |

For any source, set `model_card`, `license_file`, and `source_documents` to
workspace-relative paths and include each supplied file in `workspace.files`.
The same rule applies to `serving_manifest`, which points to the builder's
`serving.json`. Captures retain the original bytes and record file digests.
The preparation task flags missing model cards, licenses, and source-specific
provenance for review.

Checkpoint paths are relative to the captured model repository. Use the
`.workspace/` prefix for checkpoints selected in the workspace. The checkpoint
fingerprint covers the selected paths and bytes. The revision fingerprint binds
that inventory to the declared HF revision and commit. The adapter reads
generator, version, and a bounded set of runtime options from `serving.json` as
preparation inputs. Set `source.acquisition_record` to a selected workspace JSON file
such as the HF builder CPU demo’s `demo-source.json` to capture additional
options such as `max_new_tokens`. The adapter compares `serving_image` from the acquisition record
with `image` from the serving manifest. Conflicts between the declared origin
and a comparable acquisition repository or revision create a review gap and
suppress the revision fingerprint. The checkpoint inventory remains available.
Conflicting generator hints create a review gap and
serving manifest values take precedence. Omitted manual AI attribution is
recorded as unknown. Source references and static hints are supplied provenance.
Capture does not contact upstream services or create validation evidence or
training runs.

Use `workspace_model_root` to identify the workspace Triton repository, with
`.` for the workspace root. When a development repository is available, capture
compares its selected model files against the workspace selection by relative
path and digest. Paths present in only one inventory flag an incomplete comparison. Different
digests for the same path flag a byte mismatch. Repository equality still leaves the bytes loaded by the running pod
unverified. For workspace-only capture, select the config and files for each
intended model version. The original workspace paths remain in the receipt.

Recapture with a new idempotency key after changing checkpoint or workspace
bytes. Pass the previous snapshot ID to link the new preparation task to the
earlier capture. Existing requests and receipts that omit `source` retain their
behavior and digest format. No database migration is required.

See the [YOLOv8 object detection example](https://github.com/ai-lab-tech/triton-control/blob/main/examples/onnx/yolov8-object-detection-ensemble/README.md#6-capture-the-experiment-for-engineering)
for a capture based on an existing Git example and a locally edited model config.

## Consistency and security boundaries

The capture reads actual Deployment → ReplicaSet → Pod ownership using UIDs,
checks observed generation, and records current-revision Pod UIDs, restart
counts, declared images, actual pullable image identities, resource limits and
node platform where authorized node evidence is available. A mutable tag is not
resolved through a registry to invent the tested image. Bare
`containerd://sha256:...` config IDs are not represented as manifest digests.
Node read permission denial results in an explicit unknown-platform gap. Helm
RBAC adds `get` on nodes for platform observation, without node writes/listing.

Before and after copying, the pipeline compares selected model inventories,
source object versions/ETags, selected workspace contents and workload evidence.
Changes in UID/generation, image identity, Pod replacement, selected files or
instance/source binding cause a failed capture, not a mixed completed snapshot.
Re-read costs and disk use are bounded by the existing neutral capture provider's
limits: 10,000 files, 10 GiB total/per-file, depth 32 and a 120-second provider
capture budget. Enumeration itself is bounded. S3 capture retains versions when
available and uses conditional reads/rechecks otherwise. The controlled S3
**destination** always requires actual, non-null version IDs.

The model allowlist includes only intended models and selected numeric versions;
other models/versions and recognized credential paths are excluded before retained-file
count/byte limits and object reads. Enumeration still has a separate bound, including
excluded keys, so an oversized listing cannot consume unbounded work. The workspace reader verifies StatefulSet → Pod UID ownership, executes a
fixed isolated-Python read-only program in the owned workspace, opens selected
files with `O_NOFOLLOW`, rejects links/hardlinks/special files and compares
before/after content/signatures. It requires Python 3.9+ in that workspace image.
Workspace capture is limited to 200 paths, 10 MiB per file, 25 MiB total and a
30-second reader deadline. Backend decoding rechecks the exact requested path
set and sizes; arbitrary paths or extra response files cannot escape staging.
The reader does not export a container filesystem or execute project code.

Credential/runtime directories (`.aws`, `.ssh`, `.kube`, `.docker`, `.git`,
`.triton-control`, environment files, virtual environments and caches), private
key file types and common private-key/AWS-access-key signatures are rejected or
excluded. Selection plus these checks is not proof that arbitrary source code
contains no embedded secret; review project files before submitting. Raw provider
errors, workspace stderr, environment variables, shell commands, Secret values
and instance credentials are never copied into the receipt or operation status.

Snapshot identity binds canonical manifest content, the readable credential-free configuration and its identity,
intended models, runtime evidence and both model/workspace inventories. A neutral
lineage `Snapshot` binds the combined inventory without inventing MLflow IDs or
engineering transformations. Manifest self-hashes and artifact-store receipts
remain outside that identity. `manifest_digest` hashes the typed snapshot
manifest; the storage provider also hashes its enclosing storage manifest and
pins the retained bytes. These are distinct identities.

## Gaps, recapture and recovery

This is an experiment capture, not a reproducibility attestation. Explicit gaps
cover missing model files, unknown/heterogeneous running image identities,
unknown platform, changed intended model selection, missing evaluation inputs,
unverified evaluation-file references and runtime-only changes requiring a build.
Actual in-container modifications and the correspondence between current S3
bytes and models already loaded in memory cannot be proven by these observations;
`runtime_state_unverified` and `running_model_bytes_unverified` remain explicit.
Selected workspace files are marked `selected_unverified_changes` and require
retesting. Repository backend/layout qualification remains with the neutral
importer and subsequent preparation; a nonempty folder alone proves no validity.

These gaps are persisted in the immutable manifest and seed the task's
`missing_inputs` / blocked reasons. Engineers can claim the task and address them
through the existing preparation workflow. Request declarations or a healthy Pod
never silently make a release ready.

Repeat the same request with the same idempotency key to return the original
operation without recapturing. Different content under that key returns 409.
Explicitly recapture using a **new key** and `previous_snapshot_id`; the predecessor
must belong to the same submitting user and instance. The previous receipt and
files remain unchanged. Retrying a failed operation reuses the original operation
ID and can attempt capture again; only a completed capture creates a task.

Worker leases are heartbeated during bounded provider I/O and fenced again before
publication. Task, initial task event, snapshot receipt and successful operation
state commit together. Cancellation before final publication produces no task;
a completed successful operation cannot be retroactively cancelled. A crash
before commit rolls back task/receipt and permits retry; a crash after commit
cannot create a second task. Snapshot rows are protected by append-only database
triggers. No foreign key cascades from the originating instance/workspace erase
retained evidence.

Startup adds `preparation_snapshots` through the existing serialized additive
schema initializer. Existing deployments/tasks are not rewritten. Older binaries
ignore the additional table; keep its data and controlled staging during rollback.
The new API is the contract for the #22/#24 user flows; this ticket adds no new UI.
Capture registers immutable task-workspace inputs in the same transaction as the
receipt and task. After claiming the task, an eligible engineer can open its
workspace even while experiment gaps remain. Those gaps are retained unchanged:
workspace access is preparation, not evaluation or release approval.

The workspace restores the captured model inventory under `/inputs/verified/source/`
and selected project files under `/inputs/verified/source/.workspace/`. Downloads
read only the retained directory snapshot or exact retained S3 versions and verify
each size and digest. They never reread the live experiment. Assignment and storage
binding are rechecked on access. Each authorization parses the snapshot manifest
once and indexes its files by path; no authorization is cached across requests.
Changing the configured staging root, endpoint,
bucket or prefix revokes access rather than redirecting old receipts. Directory
reads reject links. Capturing an experiment does not start a workspace automatically.

Workspace staging retains its 3 GiB total and 512 KiB input-manifest limits. Larger
captures remain valid snapshots but cannot be opened through this bounded workspace
path. Empty captures have no files to register. Receipts created before storage
bindings and workspace inputs were recorded require an explicit new capture;
their origin or storage identity is never inferred during upgrade.

## Verification

Behavior/API tests: `python -m unittest tests.test_experiment_snapshots -v`.
Real-provider tests use explicitly configured disposable services:

```sh
SNAPSHOTS_TEST_DATABASE_URL=postgresql://... python -m unittest tests.test_experiment_snapshots_postgres -v
PODS_TEST_KUBECONFIG=/path/to/kubeconfig python -m unittest tests.test_experiment_snapshots_kubernetes -v
CANDIDATE_IMPORT_TEST_S3_ENDPOINT=http://127.0.0.1:9000 python -m unittest tests.test_experiment_snapshots_s3 -v
```

Backend CI runs these in its existing PostgreSQL, Kubernetes and digest-pinned SeaweedFS
jobs. The Kubernetes suite creates/deletes only a unique disposable namespace.
Tests exercise actual runtime ownership/platform reads and workspace exec,
concurrent submissions/workers, rollback/recovery, stale fences, immutable
receipts, source changes, secret exclusion and version-pinned artifact retention.

Local verification on 2026-11-16: all 41 focused tests passed with real PostgreSQL,
MinIO and Kubernetes enabled. Full backend discovery passed 1,199 tests (164
environment-dependent skips), with 82.6% coverage. Mypy (305 files), Ruff, all four
architecture contracts, Bandit and Helm lint passed. Frontend API generation,
Angular build, ESLint and Prettier passed with the updated OpenAPI copies.
