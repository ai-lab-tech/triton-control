# Model performance Jobs

Open a model's **Perf** view to configure the analyzer image, optional registry credentials, batch size, concurrency range, request count, and input data. Start creates a dedicated Kubernetes Job without a global installation. Different models can benchmark concurrently. Within one Triton instance, all versions of the same model share one active-run slot.

The analyzer image and **Optional Registry credentials (Docker config JSON)** section appear above the benchmark parameters. The image stays visible; registry credentials are collapsed by default. A pulsing speedometer and **Benchmark running** badge follow the job status returned by the backend. Preparing and stopping have separate indicators; the badge disappears after completion, failure, or cancellation. The icon does not animate when reduced motion is requested.

Creating, pending, running, and stopping runs occupy that slot. Stop targets a specific run, retains available partial output, and keeps Start disabled until its workload has terminated. Reloading or reopening the view recovers server state. Completed or failed output remains available after Job cleanup. Kubernetes scheduling and contention on the target Triton server can affect benchmark results.

Form settings are restored only from a run for the selected version, falling back to that version's legacy saved result or the form defaults. An active run for another version remains visible and blocks another start without supplying its settings to the selected version's form. If final pod logs are unavailable, previously captured output is preserved; retrieval warnings are reported separately in the run status.

Every Job runs as UID/GID 10001 with `runAsNonRoot`, filesystem group 10001, no privilege escalation, all capabilities dropped, a read-only root filesystem, RuntimeDefault seccomp, and no mounted service-account token. `/tmp` and `/dev/shm` remain writable. Custom images must support this environment; there is no root fallback. Registry credentials and JSON inputs use separate run-owned Secrets. Pod logs are captured up to 2 MB per pod.

## Configuration

Helm values under `perfAnalyzer` supply the default image, deadline, and resource limits. Corresponding environment variables are:

| Variable | Default |
| --- | --- |
| `PERF_ANALYZER_IMAGE` | `nvcr.io/nvidia/tritonserver:26.06-py3-sdk` |
| `PERF_ANALYZER_DEADLINE_SECONDS` | `3600` (minimum 60) |
| `PERF_ANALYZER_CPU_REQUEST` | `250m` |
| `PERF_ANALYZER_MEMORY_REQUEST` | `512Mi` |
| `PERF_ANALYZER_CPU_LIMIT` | `2` |
| `PERF_ANALYZER_MEMORY_LIMIT` | `2Gi` |

Jobs use the control namespace, resolved from the backend pod or `TRITON_CONTROL_NAMESPACE`/`KUBERNETES_NAMESPACE`/`POD_NAMESPACE`. Local development requires `KUBERNETES_KUBECONFIG_PATH`. The backend service account needs namespaced Job create/read/patch/delete, pod list/delete/log read, and Secret create/read/delete permissions. The chart supplies these permissions.

## Implementation

The base Job definition lives in [`perf_analyzer_job.yaml`](../triton-backend/app/services/perf_analyzer/perf_analyzer_job.yaml). It contains the suspended Job structure, security settings, default resource requests and limits, and writable temporary volumes. The template is included in the backend Python package.

[`jobs_kubernetes.py`](../triton-backend/app/services/perf_analyzer/jobs_kubernetes.py) parses a fresh template for each run with `yaml.safe_load`, then assigns the run identity, image, command, deadline, and resource overrides to the parsed fields. It attaches input and registry Secret references only when needed. Dynamic values are not inserted through raw YAML string substitution, and optional fields cannot carry over between runs.

[`commands.py`](../triton-backend/app/services/perf_analyzer/commands.py) exposes command preparation and input conversion helpers. [`jobs.py`](../triton-backend/app/services/perf_analyzer/jobs.py) handles lifecycle reconciliation and persistence; `jobs_kubernetes.py` handles Kubernetes operations. Legacy saved-result lookup remains in `installer.py`.

## API

The base path is `/api/instances/{instance_id}/models/{model_name}/perf`. Every operation enforces instance access.

| Operation | Behavior |
| --- | --- |
| `POST /runs` | Accepts version, image, optional `dockerconfigjson`, and benchmark parameters; returns 202 with durable run ID/state. |
| `GET /status?model_version=…` | Active run across versions, latest selected-version run/result, and default image. |
| `GET /runs/{run_id}` | Run state, parameters, timestamps, command, and captured output. |
| `POST /runs/{run_id}/stop` | Idempotent stop for this run only. |

A duplicate start returns 409 with the active run ID in the error detail. The former `/api/perf-analyzers` installation and synchronous execution endpoints are removed. Regenerate clients from the updated OpenAPI document.

## Upgrade and rollback

1. Stop old backend workers and drain existing singleton benchmarks before enabling the new release. This is a coordinated backend/frontend cutover; do not mix old and new execution APIs during a rolling update.
2. Apply chart RBAC and deploy the new release. Startup adds the lifecycle table and active-model unique index and adds an instance deletion flag. Existing version-specific latest-result records are preserved.
3. The reconciler retires the recorded legacy Deployment using foreground deletion and removes its pull Secret. New runs remain blocked while that installation record exists. Cleanup never deletes its namespace. If manually orphaned legacy pods remain or the recorded Deployment no longer belongs to the analyzer, inspect those resources and resolve the condition before retrying. Transient failures retry automatically.
4. Confirm independent model runs, same-model rejection, cancellation, and retained results. Instance deletion can return 409 while benchmarks stop; retry after cleanup. New benchmark admission stays disabled for an instance once deletion begins.
5. To roll back, stop new admission, drain/cancel all new runs, and confirm their pods and Secrets have been cleaned. Then deploy the previous backend/frontend and restore its singleton installation. Keep the additive database tables and saved result records.

## Verification

Run backend unit tests and coverage, mypy, Ruff, import-linter, and Bandit using the CI Python version. Run frontend tests/build/lint, regenerate OpenAPI/client artifacts, and lint/render Helm.

For database concurrency, create an isolated PostgreSQL test database and set `PERF_TEST_DATABASE_URL`, then run `python -m unittest tests.test_model_perf_jobs.PostgresPerfConcurrencyTests` from `triton-backend`. This test creates an isolated instance and exercises simultaneous reservations in separate transactions.

In a disposable Kubernetes cluster, verify two model Jobs are running together, reject a second version of either active model, inspect UID/GID and input/temp-volume access, stop one without affecting the other, restart the reconciler, rerun after termination, and confirm final output survives resource cleanup. Also exercise an incompatible image, image pull failure, and legacy retirement without namespace removal.

### Verification record — 2026-09-14

Validated in a separate Minikube Kubernetes 1.35.1 cluster (`triton-perf-test`), namespace `perf-verification`, and a dedicated PostgreSQL database. Two CPU Python models ran on Triton 26.06; benchmarks used the default 26.06 SDK image.

| Run ID | Observation |
| --- | --- |
| `e89905923b4448b7b5e5c09c3bce0e0e` | Model A ran concurrently with model B; a second version of A was rejected. Stop cancelled A while B continued running. |
| `5be7db903f7b402fa8040141a11d4ab8` | Model B ran independently and was subsequently cancelled. |
| `d5e044d5a901462ea68726e3b196c3ad` | Fresh model A run succeeded after cancellation; saved throughput output remained after Job/pod cleanup. |
| `d274a752bbfd421faa44cd9c8d791da7` | A deliberately root-dependent image failed with exit code 1 and `/root` permission denied; no root fallback occurred. |
| `65c81e3b4f2247249f1e4798f5845043` | Invalid registry produced an actionable image-pull error; the pending Job could be stopped and cleaned. |

Both simultaneous SDK containers reported `uid=10001 gid=10001 groups=10001`. Mounted JSON was readable and `/tmp` and `/dev/shm` were writable. Restarting the reconciler process preserved the original run/Job identities. After benchmark cleanup, zero benchmark Jobs or pods remained.

Legacy retirement removed the test installation `legacy-perf-1e8b079f` while preserving its namespace, unrelated Triton pod, and a result saved before migration.

Local checks passed: 381 discovered backend tests (the optional PostgreSQL concurrency test was also run explicitly), 77.7% backend coverage, 427 frontend tests, frontend build/lint/format, mypy, Ruff, four import-layer contracts, Bandit with CI Python 3.12, Helm lint/render, OpenAPI consistency, and strict OpenSpec validation.
