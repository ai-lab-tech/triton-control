# Pod state and container logs

## Operator workflow

Open an instance's **Overview** for replica readiness. Expand **Rollout details**
for the full counts and controller conditions, or **Current pods** for the pod list. Running is distinct from Ready; terminating
pods are never shown as ready. A stale observed generation is explicitly displayed.
Expand a pod for regular/init/native-sidecar state, image identity, exit codes and
previous termination reason. Missing release metadata is shown as **Not recorded**.

View logs selects the exact pod UID in the Logs tab and defaults to the regular
serving container. The resource browser lists pods and their containers, including
readiness, restart counts, init/sidecar types and observed failure reasons. A
container-specific link in Overview opens that exact container. Selecting **All
containers** opens explicitly grouped, collapsible streams.

Log options expose current/previous runs, time range and line limits. Selecting a
container loads its output even when periodic updates are paused; requests queue
behind the five-second refresh limit and retry a server throttle response. Previous
run means the last terminated instance of that container, when retained by Kubernetes.
The initial selection loads once. **Auto-refresh (10 s)** defaults to off and can be
enabled explicitly; **Refresh now** requests a single snapshot without changing that
setting. Scrolling, selecting text and searching never toggle auto-refresh. Turning
auto-refresh off discards an in-flight update. Reloading preserves the viewport scroll
offsets where possible. **Go to newest loaded entry** scrolls the snapshot without
enabling polling. Fullscreen isolates the workspace; Escape exits.

Pod state continues refreshing while automatic log refresh is off. Removed pods remain selected
with an explicit warning; a replacement is never silently substituted. Filters only
search loaded lines. Download exports the bounded loaded window, including
pod/container IDs, timestamps and partial errors, without querying more history.
Deployment apply messages remain in a separate collapsed section below runtime logs.

External registered endpoints expose endpoint health and its last observation time
only. They do not acquire fabricated replica counts or Kubernetes log access.

## API and ownership

- `GET /api/instances/{id}/pods`: schema version 1; endpoint health, live Deployment
  counts/generation/conditions, owned pods and container states.
- `GET /api/instances/{id}/pod-logs`: optional `pod_uid`, `container`, `previous`,
  `since` (timezone-aware past timestamp), `lines` and `byte_limit`.
- Both routes first enforce the existing instance-access policy (including viewers
  with read access). No caller-supplied namespace or pod name is accepted.
- Membership requires controller owner references with matching UIDs through
  Deployment → ReplicaSet → Pod; labels alone never establish access.
- Log reads resolve the current pod name from the verified UID. Ownership/UIDs are
  checked again immediately before and after the name-based Kubernetes log call;
  results are discarded if identity changed. A missing selected UID is 404; per-
  container failures in a batch are explicit partial results.
- Kubernetes failures are sanitized. Stale generation is not interpreted as a
  completed rollout; a Deployment replaced mid-observation returns 409.

`triton-control/release-id` on the pod template (annotation, or label) is the optional
release reference. Deployment/ReplicaSet revisions come from Kubernetes'
`deployment.kubernetes.io/revision`. These are reported as observed metadata,
not inferred approval/provenance. Existing deployments need neither annotation.
The response does not attest that a model inventory was validated by each pod;
that belongs to the release/readiness workflow.

## Limits and retention

The default log window is 200 lines per container and 256 KiB of aggregate log
text; hard maxima are 2,000 lines per container, 1 MiB aggregate and 16 container
streams. Metadata/JSON framing adds overhead. Truncation and omitted streams are
visible. Read timeouts apply to Kubernetes calls. `since` is translated into the
Python client's supported `since_seconds`, rounded up to a whole second.

A bounded, thread-safe in-memory throttle allows one log request per actor/instance
per five seconds **per backend worker** (429 on earlier requests). This is not a
distributed quota; installations requiring cluster-wide quotas should configure
them at their ingress. No new persisted concurrency state is introduced.

Each stream retains Kubernetes timestamps; streams are grouped, with no promise of
global clock ordering. Previous logs refer to the previous container instance in
the same pod. Kubernetes logs are **not durable retention after pod deletion**.
Use a separately configured logging backend for historical retention.

## Upgrade and compatibility

No database migration, new credentials or data backfill is required. Upgrade the
Helm RBAC before the application: `apps/replicasets` needs `get` and `list` in the
managed namespace(s). Existing pod, deployment and pod-log permissions remain.
Custom roles must receive the same read permissions.

The legacy `/api/deployments/{id}/logs` text response remains available. Its reader
now also verifies ownership, includes container identities, bounds output and
continues after partial failures. Missing deployment identity no longer falls
back to all labelled pods in a namespace. Existing health snapshot strings remain
compatible; the new UI uses the dedicated structured endpoint.

## Verification

From `triton-backend`:

```sh
python -m unittest tests.test_pods tests.test_deployments -v
PODS_TEST_KUBECONFIG=/path/to/disposable/kubeconfig python -m unittest tests.test_pods_kubernetes -v
ruff check app/ main.py
mypy app/ tests/ scripts/ main.py
lint-imports
```

The opt-in real Kubernetes test creates its own namespace and removes it afterwards.
It checks real controller ownership, init/native-sidecar/current/previous logs,
rollout replacement and unschedulable resources. CI runs it in a disposable kind
cluster; no production cluster credentials are required. Unit/API tests cover
stale observations, label spoofing/unrelated UIDs, same-name replacement races,
authorization, external endpoints, image/init failures, UTF-8 budgets, throttling
and partial output. No SQL mutation or database-concurrency contract changes in
these stories, so new PostgreSQL concurrency tests are not applicable.

From `triton-frontend`, run `npm run build`, `npm run lint` and
`npm test -- --watch=false --browsers=ChromeHeadless` (set `CHROME_BIN` as needed).
The workload component tests cover UID selection/replacement, denied access,
init failure rendering, previous/container selection, partial results, all-pod
selection, stale in-flight responses and filtering only the loaded window.

### Local verification evidence (2026-11-14)

- Backend suite: 802 tests, successful; 11 opt-in integration tests skipped in
  ordinary discovery. Coverage: 81.5% (repository minimum: 75%).
- Real provider integration: successful in a separate kind v0.33.0 / Kubernetes
  v1.37.0 cluster, including current/previous/init/sidecar log reads and rollout.
  The temporary cluster was removed after verification.
- Full frontend suite: 485 tests successful; after the final repeated-UID
  selection regression was added, all 46 affected detail/workload tests passed.
- Frontend build and ESLint, backend Ruff, mypy (224 source files), Bandit and
  all four import-layer contracts passed. Backend/frontend OpenAPI copies match.
- The existing running installation was not upgraded by this implementation.
  A release deployment must include the Helm ReplicaSet read permission above.

### Container workspace follow-up (2026-11-15)

The focused workspace adds a searchable pod/container navigator, a serving-container
default, explicit init-container handoffs, collapsible grouped output and fullscreen
with Material overlays. Angular's FullscreenOverlayContainer keeps selectors usable
in fullscreen. Pausing invalidates in-flight log updates, while pod observation stays
active. Rate-limited selections queue and retry without silently changing identity.

Validation: 54 affected Angular tests pass; ESLint and the production image build
pass. Browser checks against the Kubernetes installation exercise actual container
logs, pause-on-search, init-container selection, grouped streams, fullscreen
selectors and narrow layouts. Test model workload health is independent of the UI
rollout; its observed CrashLoopBackOff remains visible rather than being hidden.

### Explicit auto-refresh follow-up (2026-11-15)

Removed the separate pause action and all automatic pause gestures. Auto-refresh is
opt-in; pod-status refresh and manual log refresh have separate controls. Validation:
56 affected Angular tests, lint, and production image build; browser verification of
two owned model pods, initial loading with auto-refresh off, container switching, and
reading/searching without changing the toggle.

## Replacement of PR #48

This implementation supersedes the overlapping pod-visibility implementation in
[PR #48](https://github.com/ai-lab-tech/triton-control/pull/48). Only one
`GET /api/instances/{id}/pods` handler and schema should be merged.

| Contract | PR #48 | This implementation |
| --- | --- | --- |
| Envelope | `kubernetes_available`, `health_live`, `health_ready` | `schema_version: 1`, `managed`, `endpoint_live`, `endpoint_ready`, `endpoint_checked_at` |
| Controller observation | `awaiting_observation`; generation or UID changes return 409 | `stale`; generation changes mark the observation stale, UID replacement returns 409 |
| Termination | Raw Ready condition plus `deletion_timestamp` | `terminating` flag; terminating pods are not presented as ready |
| Container restarts | `restarts` | `restart_count`, plus pod-level summed `restarts` |
| Container kinds | Regular and init | Regular, init and native sidecar |
| Release metadata | Always null pending an authoritative binding | Optional observed `triton-control/release-id` annotation/label, not a provenance or approval assertion |
| API routing | Instances router / generated operation ID | Dedicated pods router / different generated operation ID |

Both versions verify controller ownership by UID and authorize instance access
before Kubernetes I/O. This replacement additionally requires both the managed
flag and `deployment_runtime == "kubernetes"`; inconsistent/non-Kubernetes records
expose endpoint health only, and log reads are rejected before provider access.

These are different proposed contracts, not interchangeable response aliases.
PR #48 has not been merged. Consumers built against that branch must regenerate
clients and migrate the fields above; no database migration is needed. The current
UI and logs provider use this versioned contract. Existing legacy text log responses
remain supported.

PR #48 also contains unrelated builder-qualification prose. It is deliberately not
included here: its referenced JSON evidence is absent from that PR. The original
[document at commit 49efdb9](https://github.com/ai-lab-tech/triton-control/blob/49efdb91b639290487f66aa8b1716e51ffe55f4b/docs/evidence/builder-qualification/README.md)
remains available for a separate builder-evidence change; closing the duplicate PR
does not delete its source branch.

### Replacement PR validation (2026-11-15)

Rebased on main after builder-qualification PR #47. Backend: 814 tests passed
(12 environment-dependent skips), 81.5% coverage; mypy passed for 233 source files.
Frontend: 496 tests passed with coverage, ESLint and full Prettier check passed.
Ruff, four architecture contracts and Bandit passed. The real Kubernetes contract
test passed again on an isolated kind v0.33.0 / Kubernetes v1.37.0 cluster (12.389s);
that cluster was deleted and the user's MicroK8s context was preserved. The generic
frontend smoke harness is delegated to CI; the specific UI workflows were exercised
against the running installation as described above.
