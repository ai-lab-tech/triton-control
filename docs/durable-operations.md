# Durable operations

The shared operation service stores execution intent before execution. It does
not replace preparation tasks, deployment records, model lineage or approvals.
There are deliberately no production adapters enabled in this foundation change:
capture, build, validation and release stories register their own adapters.
Existing deployment and Perf Analyzer endpoints keep their current behavior.

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
Kubernetes adapter tests require real Kubernetes; the foundation itself creates
no workloads. Approval, installation-mode policy and team membership checks are
mandatory domain policy where applicable, not inferred from operation state.

## Upgrade and verification

Startup adds the `operations` table and unique request constraint through the
existing advisory-lock-serialized `create_tables` mechanism. Repeated and
concurrent startup are safe for this additive table. Existing data and endpoints
are unchanged. Actor IDs intentionally retain historical identity after account
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

The API schema is exported to both checked-in OpenAPI files. No frontend screen
or generated frontend client is needed until a consuming workflow is implemented.

## Local verification record (2026-11-14)

- All 18 PostgreSQL integration tests passed: 13 operation lifecycle/concurrency
  tests, four team migration/concurrency tests, and one Perf Analyzer concurrency
  test. PostgreSQL 16 ran in an isolated disposable container.
- Full backend discovery completed: 805 tests, 782 passed and 23 skipped, no
  failures or errors. Eighteen skips are the PostgreSQL tests already executed
  separately; five chart checks require Helm, which is not installed locally.
- Full backend mypy, Ruff, four import-layer contracts, Bandit and diff whitespace
  checks previously passed; checked-in OpenAPI exports match.
- The operation-specific HTTP test checks route schema and response contract
  directly; no dedicated operation HTTP transport integration test is claimed.
- No real Kubernetes adapter was added or tested in this foundation change.
  Provider-specific fencing/recovery acceptance remains mandatory for each consumer.

