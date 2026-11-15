# Scientist handoffs

The **Submit model** navigation entry opens `/preparation/handoffs/new` in the
Control app. Active members and administrators can explicitly submit a model
object or archive, optional dataset/baseline objects and evaluation requirements.
Logging a model, uploading an artifact and running an experiment do not submit a
handoff. Submission does not run a build, validate a candidate or approve/deploy a
release.

## Source and routing contract

`POST /api/preparation/handoffs` accepts the version-one `HandoffRequest` contract.
The initial neutral adapter is `kind: s3`: a stored global S3 profile ID, a path
relative to that profile's prefix, optional version ID and expected SHA-256.
No endpoint or credentials can be supplied through the request. The authenticated
account must currently be an active member/admin. Scoped workspace/workflow tokens
cannot use this interactive endpoint. Profile access follows the existing global
member/admin S3-profile policy; engineering responsibility alone grants no source
access. Provider access denial or missing inputs referenced as objects rejects the
entire submission.

Each object is resolved using HEAD and then read using its exact non-null S3
VersionId. SHA-256 is measured from streamed bytes, not inferred from ETags. Bucket
versioning is required; the service rejects unversioned/suspended null versions.
Each input is bounded to 1 GiB and 120 seconds, read in 1 MiB chunks with bounded
connection/read timeouts. An expected digest mismatch rejects the submission.
Inputs are referenced by exact versions; this story does not copy or retain their
bytes. Storage retention must preserve those versions. Snapshot staging of live
experiments is #21; repository import is #26; optional plugin submission is #28.
Additional source kinds require authorized adapters and are not accepted as
unverified user claims.

All handoffs route to the installation's single engineering group from #17. There
is no team picker. An initialized group with no eligible engineers still receives
a queued task with `no_eligible_engineers`; no recipient notifications are made up.
An administrator can assign engineering responsibility afterwards. The shared
engineering queue/detail workflow is #24.

## Identity, retries and atomicity

A handoff identity is `(origin.installation_id, origin.record_id, revision)`.
Source origin is distinct from the measured source snapshot identity. The UI uses
the actual Control origin and profile/object reference as its source namespace.
External callers supply their own stable origin; credentials are never identities.
`revision` identifies the explicit submission, not a mutable S3 alias. Changing
requirements or recapturing changed source bytes requires a new revision.

The complete normalized request is canonically hashed using the #25 JSON contract.
The same identity/request returns the first receipt without resolving mutable
references again. The same identity with different content returns HTTP 409.
Retries recheck the active account, task access and referenced profile access.
A different caller cannot read or reuse a receipt without task authorization.

Provider reads occur before acquiring the installation engineering-group lock.
After that lock, source profile mappings and current authorization are rechecked;
then a second identity check resolves concurrent submissions. One transaction
stores the immutable receipt, preparation task, initial event and one notification
per currently eligible engineer. Any failure rolls all of them back. The unique
identity constraint is a final database guard. Requests after success return HTTP
200 with the same receipt and task identities.

The receipt contains the measured model inventory and #25 `Snapshot`, exact object
versions/checksums, submitted requirements and current authorized task state.
Absent dataset, baseline, expected behavior or acceptance criteria remain explicit
missing inputs on the task. Completeness is independent of release approval and
validation evidence; a handoff is never a release-ready assertion.

## In-app notifications

The shell's notification bell polls every 30 seconds and refreshes on opening.
It shows up to 100 recipient notifications, unread first and then newest first.
The badge displays the unread count, capped at `99+`. Links open `/preparation/handoffs/{id}`, which rechecks task
access. Notification records contain only recipient ID, receipt ID, timestamps
and read state. Display text is fixed: no model title, object path, evaluation
content, credentials or provider error strings are copied into notifications.

`GET /api/preparation/notifications` is recipient-scoped and paginated (maximum
100). `PUT /api/preparation/notifications/{id}/read` is recipient-scoped and
idempotent. Revoked engineering membership does not erase a historical generic
notification, but denies receipt access unless the user otherwise retains task
access. No external notification transport is included.

## Additive upgrade and verification

Startup adds `preparation_handoffs` and `handoff_notifications`; existing tasks and
API paths remain unchanged. Receipt UPDATE/DELETE is rejected by database triggers
on PostgreSQL and SQLite. Notification read state is mutable. Existing tasks do not
receive invented handoffs or retroactive notifications. Task/history creation uses
the #18 transaction hook. Both OpenAPI copies and generated-client compatibility
must be checked when changing the contract.

Focused behavior and authorization tests:

```bash
cd triton-backend
python -m unittest tests.test_handoffs -v
PREPARATION_TEST_DATABASE_URL=postgresql://USER:PASSWORD@HOST/DB \
  python -m unittest tests.test_handoffs_postgres -v
```

The PostgreSQL suite includes simultaneous identical and conflicting submissions,
rollback, append-only receipts and additive startup. The CI PostgreSQL job runs it.
For isolated MinIO (test credentials `handoff_test` / `handoff_test_password`):

```bash
HANDOFF_TEST_S3_ENDPOINT=http://127.0.0.1:9000 \
  python -m unittest tests.test_handoff_s3_integration -v
```

The MinIO suite creates disposable versioned buckets, proves that a pinned version
survives replacement of the latest object, and deletes all test versions afterwards.
No Kubernetes workload/provider behavior is introduced by this story.

## Implementation validation

Local verification for this change:

- Full backend discovery: 993 tests successful, 50 environment-dependent tests
  skipped; overall coverage 82.7%. PostgreSQL preparation tests and real MinIO
  source tests were enabled during this run.
- Final focused handoff suites: 43 tests successful, including real PostgreSQL
  concurrency and real MinIO version replacement/access checks.
- Full frontend suite: 514 tests successful in headless Chromium, including seven
  new form, receipt, retry, authorization and notification tests.
- Frontend build and ESLint; backend Ruff, mypy (270 files), four architecture
  contracts and Bandit passed. Both OpenAPI copies were exported together and the
  frontend client regenerated.

CI execution is pending publication of the branch. These local results do not
claim that the feature has been deployed or that the later #24 workflow exists.
