# Serving image builds from a task workspace

The serving image build UI uses the task workspace and durable build contracts. API
controllers delegate
to services; repositories own SQL; schemas perform no I/O. The extension separates
VS Code interaction, HTTP transport and bounded filesystem snapshots.

## Start here: create and claim a task

A preparation task records the model you are working on and who is responsible
for it. Claiming the task assigns it to you. Image builds must run from that
assigned task's workspace, not a personal workspace.

1. **Enable engineering access.** Ask an administrator to open **User Management** from the account menu,
   select your name, and check **ML engineer**. Your account must also have member or
   administrator access.
2. Open **Development → Engineering queue → Submit model**.
3. **Choose your model.** Enter a handoff title, select an S3 profile, enter the
   model's object path and set a submission revision, for example `1`. The object
   must already exist, and the bucket must have versioning enabled. Paths are
   relative to the S3 profile's prefix.
4. **Fill in the evaluation requirements:** the dataset object path, baseline
   object path, expected behavior and acceptance criteria. Although the submission
   form allows these fields to be omitted, all four are needed to open the task
   workspace. The dataset and baseline objects must already exist in S3.
5. **Click Submit to engineering.** This creates a preparation task and opens
   its handoff receipt.
6. **Click Claim task** on the receipt to assign the task to yourself.
7. **Click Open task workspace** and wait for code-server to start.
8. In code-server, press **F1** and choose **Triton Control: Build serving image**.
   Follow the build steps below.

If **Open task workspace** is disabled, check that the task is assigned and no
required inputs are missing. Only the current assignee can open its normal task
workspace. If you submitted incomplete inputs, select **Add missing information**.
Complete the prefilled form, submit the revision and claim the new task.

For more detail, see [engineering access](user-management.md),
[model handoffs](scientist-handoffs.md) and [task workspaces](task-workspaces.md).

## Workflow

1. Claim a preparation task and open its verified task workspace.
2. In code-server choose **Triton Control: Build serving image**, from the command
   palette, Workspace toolbar or the context menu on a Dockerfile or its folder. The file action accepts lowercase
   `dockerfile` and builds directly from that file without S3.
   The folder action sends files from the selected folder and its subfolders to the
   Triton Control backend for `COPY` and `ADD`. Before sending them, the extension
   excludes known credential paths such as `.env`, `.ssh/` and `.aws/`.
   The backend then applies `.dockerignore` and uploads the filtered archive to S3.
   For example, model weights excluded by `.dockerignore` still count toward the
   upload limit between code-server and the backend. The S3 archive omits them.
3. Enter the image name and tag, for example `opt-125m:v1` for a local archive.
   For registry publishing, the field starts with the configured repository.
4. Only the folder action uses S3. If several storage profiles are available, select one for the folder.
   The extension uploads the context and starts the build.
5. Watch queued/building/pushing progress in **Triton Control Builds**. Pushing is
   informational progress observed from executor output; only a successful owned
   executor termination with a valid digest can produce a candidate result.
6. Use **Inspect serving builds** for refresh, authorized attempt logs, cancellation,
   retry, or copying an exact candidate image. Reopen this command after editor restart
   to recover task history. Repeating an unchanged submission reuses its idempotency
   key; a failed build is retried explicitly as a new numbered attempt.

The handoff receipt links to `/preparation/tasks/:taskId/builds`. This task page lists
build history, logs, exact candidate image references and cancel/retry actions. After cleanup, **Retry build** starts another attempt using the same saved files.
Each page contains up to 50 builds. Use the extension to capture a new local context.
Expired contexts require a new snapshot: repeat **Build serving image**, even if the files are unchanged. An expired, failed or cancelled context
is replaced with a new context key. That key is saved before admission so a lost
response or editor restart resumes the same replacement. Pending and usable contexts
retain their keys; build retries never silently recapture source files.

Build success is **not production readiness**, correctness validation, approval, or
release publication. The durable operation remains associated with its task; its
attempt records retain context identity, executor, unique destination and reported
digest for downstream candidate/validation integration. No mutable tag lookup is used
as a substitute for an executor result.

## Authorization and configuration

The installation must have a qualified, enabled build policy and its configured
storage/credentials. `/api/builds/options?task_id=...` returns only authorized repository
names and the context byte limit, never Secret names, storage credentials or executor
configuration. The local snapshot cap is the smaller of installation policy and 64 MiB.

The extension uses the existing managed workspace credential plus
`X-Triton-Build-Workspace: namespace/name`. The backend validates the current Kubernetes
Secret, active owner, assignment and source access on each request. Only the reviewed
build/context paths accept that header. Personal workspaces and admin recovery
workspaces cannot use this build channel; deployment tokens remain rejected. The
workspace task must match the context/build being accessed. General browser/API users
retain existing task/team and operation ownership checks.

Production denies these build APIs, including reads and direct workspace calls. The
mode check uses the existing `TRITON_CONTROL_MODE` contract and does not depend on
optional MLflow. The installation capability UI is not
required by the build controls. Administrators do not bypass development-only build policy.

## Upgrade and verification

No new tables or columns are required. `BuildContextRequest.task_id` is optional and
stored in the existing operation JSON; omitted values preserve existing context
idempotency identities. Old unbound contexts remain usable by ordinary authorized API
clients, but workspace credentials can use only contexts bound to their own task.
New task-list/options/retry routes are additive. Deploy the updated backend, which
packages extension version 0.4.15 into workspace ConfigMaps. Existing workspaces need their extension
ConfigMap refreshed and code-server restarted as part of the workspace rollout;
backend deployment alone does not modify their existing ConfigMaps.

AAA tests cover archive boundaries, the real Node-to-Python archive contract, wrong-task
access, revoked credentials, production restrictions, failed builds, distinct retry
attempts, digest formatting and uncertain-request replay. PostgreSQL tests use isolated
schemas (`BUILD_TEST_DATABASE_URL`); Kubernetes provider tests use disposable namespaces
(`BUILD_TEST_KUBERNETES=1`, `KUBERNETES_KUBECONFIG_PATH`). These provider fixtures verify
lifecycle behavior and do not qualify a real builder or claim a production image digest.

### Local validation evidence (2026-11-17)

- Backend discovery: 1,478 tests, passed with 302 opt-in tests skipped.
- Frontend ChromeHeadless: 536 passed; Prettier and TypeScript checks passed.
- Extension: 122 passed, 7 opt-in integration tests skipped.
- PostgreSQL: operation/build concurrency and UI authorization runs passed; final
  UI/snapshot run completed 62 tests against SQLite and isolated PostgreSQL schemas.
- Kubernetes: all 3 real build lifecycle tests passed in disposable namespaces.
- Ruff (application and new tests), mypy, all 4 import-layer contracts, Bandit,
  generated OpenAPI copies and `git diff --check` passed.

The existing `test_development.py` has pre-existing long-line lint findings outside
this change; the backend CI Ruff scope is application code. No real image build,
registry push, release approval or production deployment was performed by these tests.

### Code-server browser smoke

The existing `Code-server plugin smoke` workflow includes the serving-build commands.
Run it locally with `bash triton-frontend/e2e/code-server/run.sh` (Docker, kind,
kubectl, Helm, Playwright Chromium, and Python with boto3/packaging are required).
It creates and removes an isolated cluster.

The browser tests exercise the deployed VSIX in real code-server: both commands
explain the task requirement in a personal workspace. A versioned SeaweedFS handoff,
engineering-group membership and claimed task provision a real task workspace;
its extension reads empty build history using the managed workspace credential.
The default development builder opens the Dockerfile picker for the assigned task.
Cancelling the picker leaves the task’s build history empty. The task workspace is removed after the suite so the
existing personal-workspace restart/upgrade smoke remains independent.

This smoke does not build or push an image. Successful execution, progress, logs,
retry and exact digest handling have separate unit/provider coverage; testing them
end to end requires the qualified builder, trusted registry and staging fixture.
