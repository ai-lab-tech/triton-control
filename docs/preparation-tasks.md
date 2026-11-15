# Preparation tasks

S02-B provides the backend engineering queue, assignment transitions and retained
history. It does not submit scientist handoffs (S03-A), capture experiment snapshots
(S03-B), open workspaces (S04-A), or implement the queue/detail UI (S04-B).

## Persistence and integration

Tasks use UUIDs, an installation engineering team, an optional individual assignee,
a submitter, optional immutable source-snapshot identifier, a state and an integer
revision starting at 1. Snapshot identifiers are references only: this task API
neither returns source bytes nor grants model, dataset, instance or workspace access.
The handoff/snapshot caller must authorize and verify a snapshot before attaching
its identifier. Missing inputs are bounded machine-readable names, not file contents.

`app.services.preparation.create_task(session, claims, TaskCreate(...))` is the
internal creation hook. It routes work to the existing installation engineering
group from S02-A. Initialize that group through User Management or
`PUT /api/teams/engineering` before creating tasks. An empty group still accepts tasks
and exposes `no_eligible_engineers`; adding eligible members makes them actionable.
No task-creation HTTP endpoint is introduced ahead of the handoff contract.

The future handoff service can call `create_task(..., commit=False)` to persist a
task, its initial event and its own deduplication/notification records in one
transaction. The caller must commit on success or roll back on failure. The task
service does not invent handoff identities, deduplication receipts or notifications.

`record_attempt(...)` is an internal, revision-checked hook for recording an actual
completed `EngineeringAttempt` using S05-A's source/input/output identities. It
retains the attempt separately and leaves task state unchanged. Duplicate origins
within a task conflict. It does not run conversions or fabricate successful
outputs for failed builds; execution lifecycle and failures belong to their own
attempt/build/operation contracts.

## Authorization

Every request resolves the current active local/OIDC account and rechecks its
current role and membership. Token role claims alone do not authorize actions.

| Actor                      | Read queue/detail/history                                              | Mutate                                                                                                       |
| -------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Admin                      | All tasks                                                              | Request/resolve information, release claims, reassign and cancel. Claim requires engineering membership too. |
| Eligible engineer          | Tasks assigned to their current active team                            | Claim unassigned tasks; current assignee may release, request/resolve information and cancel.                |
| Submitter                  | Own tasks                                                              | Active member/admin may cancel their own task. Other engineering actions require engineering eligibility.    |
| Viewer                     | Own submitted tasks only                                               | None.                                                                                                        |
| Unrelated/revoked engineer | No team-task access unless independently authorized as submitter/admin | None through the old assignment.                                                                             |

Eligibility requires an active team, active membership and active member/admin
account, matching S02-A. Retained assignee IDs do not preserve access after
revocation or account deletion. An admin can reassign an orphaned task or release
its claim; no previous events or attempts are removed. Team membership gives no
additional access to the source artifact or other resources.

## API and transitions

Base path: `/api/preparation-tasks`.

| Method and suffix                | Purpose                                                                          |
| -------------------------------- | -------------------------------------------------------------------------------- |
| `GET /`                          | Authorized queue, oldest first, with `limit` (1–100) and `offset` (0–1,000,000). |
| `GET /{id}`                      | Current task, revision and computed blocked reasons.                             |
| `GET /{id}/events`               | Latest events first; bounded `limit`/`offset`.                                   |
| `GET /{id}/attempts`             | Actual recorded attempts, oldest first; bounded `limit`/`offset`.                |
| `POST /{id}/claim`               | Atomically assign an unassigned task to the caller.                              |
| `POST /{id}/release-claim`       | Remove the assignment, retaining missing inputs and history.                     |
| `POST /{id}/request-information` | Add missing-input names and enter `awaiting_information`.                        |
| `POST /{id}/resolve-inputs`      | Resolve named missing inputs with an explanatory reason.                         |
| `POST /{id}/reassign`            | Admin assignment to a currently eligible engineer, or `null` to clear.           |
| `POST /{id}/cancel`              | Cancel the task while retaining history and attempts.                            |

Every POST requires `expected_revision`, an integer matching the current task.
Information requests additionally require nonempty `missing_inputs` and `reason`;
resolution uses nonempty `resolved_inputs` and `reason`. Cancellation/reassignment
require `reason`; reassignment accepts `assignee_user_id` (integer or null).
Unknown request fields, invalid IDs, empty reasons and invalid bounds return 422.
Unauthorized use returns 403; missing tasks 404; stale revisions, competing claims,
invalid assignees or illegal transitions return 409. Reload after a conflict rather
than blindly retrying an old revision.

Example claim:

```json
{ "expected_revision": 1 }
```

A new task starts `queued`. Claiming enters `claimed`, even when evaluation inputs
are missing, so its engineer can resolve them. Requesting information enters
`awaiting_information` without discarding the claim. Resolving the last missing
input returns to `claimed` or `queued` depending on assignment. Release/reassignment
preserves an explicit wait for information. Cancellation is terminal in this
contract; a new request needs a separate task. Build status and deployment status
never overwrite these task states. Input resolution describes preparation
completeness and is not a runtime test result or production approval.

`blocked_reasons` is computed from current facts: `missing_input:<name>`,
`no_eligible_engineers`, `assignee_ineligible`, and `cancelled`. It is deliberately
separate from stored task state. Requests and resolutions retain their reason and
before/after values in history. Do not put credentials or confidential data into
free-text titles/reasons.

## Concurrency, retention and upgrade

PostgreSQL mutations lock the task, check the supplied revision and lock the team
row used by membership updates before checking eligibility. A successful mutation
increments the revision and appends exactly one event in the same transaction.
Two claims with the same revision produce exactly one winner. Failure before
commit rolls back both task changes and history; a lost response can be reconciled
by reading the current task/history. Repeating a stale action returns 409.

Startup additively creates `preparation_tasks`, `preparation_task_events` and
`preparation_attempts`, their constraints, and append-only history triggers through
the existing serialized schema initializer. It neither converts existing
deployments into tasks nor modifies users, memberships or operation records.
PostgreSQL installs a trigger function and UPDATE/DELETE guards; SQLite uses
matching guards for unit tests. Startup uses the existing schema-owning database
account, including permission to create functions/triggers. Database owners remain
able to perform explicit maintenance; the API exposes no history-edit/delete or
task-delete route. User IDs are retained historical identities so account removal
cannot cascade into lost task evidence.

Repeated and concurrent startup preserve existing records. Rollback to an older
application can leave these additive tables in place; export retained audit data
before any deliberate destructive schema removal. DTOs currently expose
`schema_version: 1`; consumers must tolerate additive future endpoints while
requests reject unknown fields.

## Verification

```bash
cd triton-backend
python -m unittest tests.test_preparation -v
PREPARATION_TEST_DATABASE_URL=postgresql://test:test@localhost:5432/test \
  python -m unittest tests.test_preparation_postgres -v
```

The PostgreSQL suite uses isolated temporary schemas, reruns the behavior tests,
and checks independent-session claim races, membership revocation, stale sessions,
failed audit writes and concurrent full startup. CI runs it against the existing
PostgreSQL 16 service. This story performs no Kubernetes/provider operations;
Kubernetes runtime verification belongs to the corresponding workload stories.

### Local implementation evidence (2026-11-15)

- Full backend discovery: 915 tests, successful; 75 environment-dependent tests
  skipped in that invocation. Overall coverage: 82.1%; preparation modules: 95.6%.
- Dedicated preparation and prerequisite-team suites with PostgreSQL 16: 56 tests,
  successful, including concurrent claims and upgrade/recovery checks.
- Strict mypy: 256 files checked; Ruff, all four import-linter contracts and Bandit
  passed. Backend/frontend OpenAPI documents match, and the frontend client was
  regenerated from that document.

The PostgreSQL results above are from a separate invocation with the explicit test
URLs, so those tests were exercised despite being skipped by default discovery.
