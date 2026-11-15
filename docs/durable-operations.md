# Durable operations

The shared operation service stores execution intent before execution. It does
not replace preparation tasks, deployment records, model lineage or approvals.
Managed development deployment creation is the first consumer (`deployment.create.v1`).
Capture, build, validation and release stories register their own adapters.
Perf Analyzer keeps its existing reconciliation. There is no production executor in this adapter.

## HTTP contract

- `POST /api/operations`, with `Idempotency-Key` and `{kind, target, payload}`,
  returns **202**, an operation ID, `status_url`, and a `Location` header.
- `GET /api/operations/{id}` returns the authorized current status.
- `POST /api/operations/{id}/cancel` records cancellation intent (202).
- `POST /api/operations/{id}/retry` retries a failed operation (202); other
  states return 409. The operation/resource identity remains stable.

Only application-registered kinds are accepted; unknown kinds return 422. No
arbitrary commands or provider methods are exposed. A required domain adapter
checks current target permissions for every submission, read, cancel, retry,
execution, and execution checkpoint. Current active-user and write-role checks
also apply; stored role claims are not treated as permanent authority.
Read policy (owner, team, administrators) must be specified by the domain adapter.

The unique scope is actor + kind + target + key. Adapters validate input and
normalize defaults; sorted finite JSON is hashed after normalization. Object-key
order is ignored; array order and JSON numeric types remain significant unless
the adapter normalizes them. Same input reuses the ID, including after completion;
changed input under the same scope/key returns 409. A deliberately new operation
uses a new key. Use stable target IDs, not mutable aliases. Inputs are limited to
64 KiB and must contain credential references, never secret values. Payloads,
keys and worker internals are not returned by the public status DTO.

## Execution and recovery

Committed rows are the durable queue, so an enqueue callback cannot be lost.
Workers poll enabled kinds, reserve due rows with `FOR UPDATE SKIP LOCKED`, and
commit a 30-second lease with an incremented generation. PostgreSQL wall time
controls leases. Calls must be bounded; adapters invoke `context.checkpoint()`
regularly and immediately before external transitions. This renews the lease,
checks current policy and detects cancellation. Completion and renewal reject
expired or superseded generations. Restarted workers reclaim expired leases.

Every reconciliation observes external state under the stable operation ID
before writing. A transport error leaves a pending operation with capped backoff;
raw exception text is not exposed. An uncertain write must NEVER be classified
as a definitive failure just because its acknowledgement was lost. Explicit
adapter failures may be retried using the same identity, again observing first.

Database fencing alone cannot stop an old process from finishing an external
request. Adapters MUST use deterministic resource identities, ownership checks,
and provider preconditions (for example Kubernetes UID/resourceVersion checks)
or provider-supported fencing tokens. They must not write unguarded domain state
from a stale attempt. Verify current leases/generations in the transaction that
publishes domain changes. Cross-operation ordering for the same target is a
separate domain responsibility; distinct requests are not implicitly serialized.
No exactly-once external execution guarantee is claimed.

Cancellation remains a request until the adapter confirms cleanup or a safe
terminal outcome. A concurrent success cannot overwrite cancellation intent.
Revoked execution access blocks further work without dropping the intent; an
adapter's policy must explicitly decide who can clean up existing workloads.
Blocked operations remain pending with an explanatory message and can resume
when authorization is restored. No generic privilege bypass is supplied.

## Adapter contract

Register adapters once during application initialization, before workers start:
`service.register("capture.v1", adapter)`. Register the same set on every replica.
Version kinds when changing input semantics; retain old adapters until their
operations drain. Registration is application code, not runtime user configuration.

Implement `normalize(payload)`, `authorize(session, actor, target, action)` and
`reconcile(operation, context)`. Reconciliation returns `ReconcileResult` with
pending/succeeded/failed/cancelled, a safe result, safe message and retry delay.
Domain task/build/release states remain in the domain adapter. Success must mean
verified domain success, never merely a successful HTTP request to a provider.

Before enabling an adapter, test it against its real provider, including lost
acknowledgements, ownership collisions, concurrent stale attempts and cleanup.
Kubernetes adapter tests require real Kubernetes. Approval, installation-mode policy and team membership checks are
mandatory domain policy where applicable, not inferred from operation state.

## Upgrade and verification

Startup adds the `operations` and `deployment_intents` tables and unique constraints through the
existing advisory-lock-serialized `create_tables` mechanism. Repeated and
concurrent startup are safe for this additive table. Existing instance rows are retained. Actor IDs intentionally retain historical identity after account
deletion; execution requires a currently existing active account. No automatic
purge is supplied: removing rows also removes their idempotency guarantee.

Run `python -m unittest tests.test_operations -v` for lifecycle/HTTP-contract tests.
Run with a disposable PostgreSQL database:

```sh
OPERATIONS_TEST_DATABASE_URL=postgresql://user:password@localhost/database \
  python -m unittest tests.test_operations_postgres -v
```

Integration tests create isolated temporary schemas and require CREATE SCHEMA
permission. They cover concurrent reservations/claims, repeated schema creation,
lease expiry, old-worker rejection, crash recovery, uncertain writes,
authorization revocation and cancellation races. The PostgreSQL CI job runs them
alongside team migration tests. SQLite tests are not evidence for PostgreSQL locks.

The API schema is exported to both checked-in OpenAPI files and the frontend client is regenerated.

## Managed development deployments

`POST /api/deployments` now returns **202 Accepted**. The existing response fields
(including `instance_id`) remain; `operation_id`, `status_url`, `operation_state`
and a `Location` header are added. `applied_resources` is empty until success.
The endpoint commits the operation, instance, assignment and frozen intent in one
transaction before any Kubernetes writes. PostgreSQL serializes target admission
and assignment updates; a separate request for the same reserved identity gets 409.

Use a persistent `Idempotency-Key`. The scope is actor + deployment kind + normalized
name + key. A keyed HMAC of the canonical request includes secret input without
putting it in public operation JSON. Replay rechecks account, instance and delegated
token restrictions, and reuses the original rendered manifests, resolved S3 profile,
registry credentials and generated auth names. Changed input under the same key
returns 409. A replay for a deleted instance returns 409; create afresh with a new key.

Legacy clients without a key receive a deterministic actor/target-scoped key derived
from the same canonical request. This makes an identical repeat safe, including a
lost response; changing input for an existing name is still a conflict. New clients
should supply their own key. The browser stores only the key, request fingerprint
and name in session storage, scoped to the signed-in user. It recovers the instance
through `GET /api/deployments/requests/{key}?deployment_name=...`. The instance page
polls `GET /api/deployments/{id}/operation`, shows queued/running/failed/completed or
cancellation requested, and offers retry/cancel. Legacy instances return null status.

Frozen manifests and credential values use authenticated Fernet encryption in the
internal `deployment_intents.bundle_enc` column. Configure a stable, strong
`OPERATION_SECRET_ENCRYPTION_KEY`; the existing stable `SESSION_SECRET` is the
fallback. Every replica must use the same key. Keep it with database backups;
changing/loss of this key prevents recovery and changes request fingerprints.
There is no plaintext fallback and no key rotation/re-encryption command in this
change. The operation status contains neither inputs nor credentials. Existing
instance credential storage is unchanged by this mechanism.

The reconciler uses create-only requests, stable resource names, an operation label,
a ConfigMap ownership anchor and Kubernetes owner references. It reads before
creating and verifies frozen content, ownership and recorded UIDs. Deletes use UID
and resourceVersion preconditions. A conflicting or modified resource fails safely;
it is never adopted merely because the name matches. Partial writes and lost
acknowledgements are observed on the next pass. Domain updates are fenced by the
current leased operation row, locked during bounded provider calls. Ownership
checks and the anchor protect against late writes beyond database fencing.

Creation succeeds only when Kubernetes reports the current Deployment generation,
all desired replicas updated, ready and available. The pod readiness probe is the
existing configured Triton serving probe. This is creation readiness at that point,
not a promise that an individual MLflow model is loaded or the service stays healthy.
Live health remains on the instance. The default readiness deadline is 1800 seconds
(`TRITON_DEPLOY_OPERATION_TIMEOUT_SECONDS`, minimum 60). Readiness timeout and
invalid provider input fail with safe messages; uncertain provider outcomes retry
with backoff. Explicit retry retains the same resource identities and resets the
deadline. Optional inference-example capture remains best effort.

Current active account, role, instance assignment, account credential version,
source profile existence and endpoint policy are checked again during execution.
`TRITON_CONTROL_MODE` defaults to `development`; other values block new creation and
retry execution. Requested cleanup is still permitted after a mode cutover. Revoked
account/instance access blocks the original actor's execution, including cleanup;
an administrator must restore authorized access or perform reviewed manual cleanup.
No operation grants additional resource privileges. Scoped MLflow/workspace tokens
retain their existing API restrictions and use the MLflow status endpoint.

Cancellation deletes only the frozen, owned resource set and ownership anchor,
waits for their pods to disappear, and leaves the namespace and external TLS Secrets
intact. Ownership conflicts during cleanup remain observable and retryable pending
cancellation. No cleanup bypass is granted for a foreign or replaced resource.

Deletion of a new managed instance first requests cleanup and returns **409** with
“cleanup requested”. Repeat the same DELETE after cleanup; only then is the instance
removed and its identity reservation released. This also serializes deletion with
an in-flight worker. Old workers cannot recreate a deliberately deleted instance.
Operation/instance IDs and safe history remain for diagnosis. Post-creation manual
resource replacement or access reconfiguration can require manual inspection if
it changes resource ownership; the creation record is not a general update reconciler.

Pre-upgrade pending instances have no frozen input. They are **not** automatically
replayed or assigned fabricated operations. Inspect and explicitly remove/recreate
those instances. Deploy application replicas with a consistent version; drain the
old BackgroundTasks workers before switching to the durable admission endpoint.

## Optional MLflow client compatibility

Reviewed `mlflow-plugin/packages/mlflow-triton-control/src/mlflow_triton_control/deployment_client.py`:
it uses `raise_for_status()`, consumes `instance_id`, and polls the existing MLflow
model-readiness endpoint. It accepts 202 without modification. Keyless retries
therefore work, but this client does not display the operation or offer operation
retry/cancel. A failed creation remains visible through instance status until the
client's readiness timeout. Its synchronous DELETE caller must retry the explicit
cleanup-pending 409; it must not treat this response as completed deletion. The
published-client compatibility runner now exercises this migration behavior.

## Verification

Backend tests cover atomic admission/rollback, secret-safe status, current permissions,
canonical replay, conflicting inputs and target reservations, same-actor assignment
races, concurrent workers, lease loss, crash recovery, uncertain writes, readiness
failure/retry and owned cleanup. UI tests cover stable retry keys, reload recovery,
status polling, cancellation and retry. An HTTP contract test exercises the existing
keyless client's response/readiness/deletion behavior through the real routes.

```sh
python -m unittest tests.test_deployment_operations tests.test_deployment_client_contract -v
OPERATIONS_TEST_DATABASE_URL=postgresql://user:password@localhost/database \
  python -m unittest tests.test_operations_postgres tests.test_deployment_operations_postgres -v
DEPLOYMENT_TEST_KUBERNETES=1 \
KUBERNETES_KUBECONFIG_PATH=/path/to/kubeconfig \
OPERATIONS_TEST_DATABASE_URL=postgresql://user:password@localhost/database \
  python -m unittest tests.test_deployment_operations_kubernetes -v
```

Kubernetes tests create and remove unique `tc-operation-test-*` namespaces and
isolated database schemas. They check actual UIDs, object counts, lost acknowledgements,
foreign objects, stale-worker rejection, timeout/retry, deletion and cleanup. A small
HTTP probe fixture verifies real controller readiness; it does not qualify Triton
inference, GPU runtime, S3 connectivity or a production image. CI runs these tests
with PostgreSQL and kind. Local verification uses PostgreSQL 16 and MicroK8s.

### Local verification record (2026-11-14)

- Full backend coverage run: 835 tests passed, no skips; 81.7% line/branch coverage.
  This includes PostgreSQL operation/team/Perf Analyzer tests, the real Kubernetes
  deployment tests, the HTTP client contract fixture and all five Helm chart checks.
- An additional PostgreSQL test passed for deletion racing an in-flight deployment
  write, including cleanup and prevention of recreation. A targeted recovery test
  also verifies lease renewal during slow reads of already-created resources.
- Backend mypy (232 files), Ruff, Bandit, all four import-layer contracts, OpenAPI
  parity and Helm lint passed.
- Frontend build, lint and 481 browser tests passed, including request-key replay,
  reload recovery and operation status/actions.
- The compatibility HTTP fixture was tested locally; installing and exercising the
  published MLflow distribution remains the existing dedicated CI compatibility job.
- npm audit still reports advisories in existing dependencies; none concern the added
  `@noble/hashes` dependency. Dependency upgrades are outside this deployment change.
- Disposable PostgreSQL and unique Kubernetes test namespaces were removed after testing.
