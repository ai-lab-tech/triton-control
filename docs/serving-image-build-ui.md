# Serving image builds from a task workspace

Implements concept story [S06-D / #32](https://github.com/ai-lab-tech/triton-control-concepts/issues/32)
on the S04-A task workspace and S06-C durable build contracts. API controllers delegate
to services; repositories own SQL; schemas perform no I/O. The extension separates
VS Code interaction, HTTP transport and bounded filesystem snapshots.

## Workflow

1. Claim a preparation task and open its verified task workspace.
2. In code-server choose **Triton Control: Build serving image**, from the command
   palette, Workspace toolbar or folder context menu.
3. Select a context directory, its relative Dockerfile path, a permitted destination
   repository, exact external base image references (`repository@sha256:...`) and
   optional dependency lock files. Use no base references for `FROM scratch`.
4. Review the upload confirmation. The extension freezes file bytes in a bounded
   POSIX tar, rejects links/special files and common embedded credential signatures,
   and excludes known credential paths. The backend verifies the archive hash,
   applies Dockerignore and records the retained immutable context and its hashes.
   Dockerignore is applied on the server: select a directory containing only files
   you intend to upload. Unknown secrets cannot be detected reliably by patterns.
5. Watch queued/building/pushing progress in **Triton Control Builds**. Pushing is
   informational progress observed from executor output; only a successful owned
   executor termination with a valid digest can produce a candidate result.
6. Use **Inspect serving builds** for refresh, authorized attempt logs, cancellation,
   retry, or copying an exact candidate image. Reopen this command after editor restart
   to recover task history. Repeating an unchanged submission reuses its idempotency
   key; a failed build is retried explicitly as a new numbered attempt and unique tag.

The handoff receipt links to `/preparation/tasks/:taskId/builds`. This task page lists
build history, logs, exact candidate image references and cancel/retry actions. It can
also start a build from an existing verified context ID and permitted repository.
Each page contains up to 50 builds. Use the extension to capture a new local context.
Expired contexts require a new snapshot; retries never silently recapture source files.

Build success is **not production readiness**, correctness validation, approval, or
release publication. The durable operation remains associated with its task; its
attempt records retain context identity, executor, unique destination and reported
digest for downstream candidate/validation integration. No mutable tag lookup is used
as a substitute for an executor result.

## Authorization and configuration

The installation must have a qualified, enabled S06 build policy and its configured
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
optional MLflow. The separately developed S01-B installation capability UI is not
required by this branch. Administrators do not bypass development-only build policy.

## Upgrade and verification

No new tables or columns are required. `BuildContextRequest.task_id` is optional and
stored in the existing operation JSON; omitted values preserve existing context
idempotency identities. Old unbound contexts remain usable by ordinary authorized API
clients, but workspace credentials can use only contexts bound to their own task.
New task-list/options/retry routes are additive. Deploy the updated backend, which
packages extension version 0.4.12 into workspace ConfigMaps. Existing workspaces need their extension
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
explain the task requirement in a personal workspace. A versioned MinIO handoff,
engineering-group membership and claimed task provision a real task workspace;
its extension reads empty build history using the managed workspace credential.
Build submission is denied before context selection because this installation has
no qualified build policy. The task workspace is removed after the suite so the
existing personal-workspace restart/upgrade smoke remains independent.

This smoke does not build or push an image. Successful execution, progress, logs,
retry and exact digest handling have separate unit/provider coverage; testing them
end to end requires the qualified S06 builder, trusted registry and staging fixture.
