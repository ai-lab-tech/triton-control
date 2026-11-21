# Recoverable development runtime builds

`POST /api/builds` accepts `task_id`, a verified `context_id`, an exact allowed
`repository`, and an `Idempotency-Key` header. It returns 202 and a `Location`.
Use `GET /api/builds/{id}` for operation and attempt records,
`GET /api/builds/{id}/logs?attempt=1` for logs, and
`POST /api/builds/{id}/cancel` for durable cancellation. Failed, cleaned attempts
can be retried through `POST /api/operations/{id}/retry`.

The actor must be an active installation engineer and a current member of the
task's engineering team, or an administrator. Reads, logs, cancellation and
execution recheck current access; another engineer cannot inspect the owner's
build. Delegated deployment tokens cannot use these endpoints. Production cannot
admit or activate builds. Operators can still inspect retained evidence and
cancel existing work after disabling execution.

## Provisioning and qualification

Follow [builder provisioning](build-provisioning.md) and
[context staging](build-contexts.md). Execution additionally requires:

- A dedicated development namespace, separate from the backend and system
  namespaces, with the qualified NetworkPolicy and enforcing CNI.
- `policy.enabled=true` and `TRITON_BUILD_FIXTURE_REVISION` set to the reviewed
  qualification receipt's revision (`buildQualification.fixtureRevision` in Helm).
- A passing receipt for that exact policy, executor digest, platform and fixture
  revision, including `versioned_https_context`. Older builder qualification receipts lack this
  check and cannot enable execution. Re-run `scripts/qualify_builder.py` after
  upgrading. Qualification is never fabricated by the API or the tests.
- A versioned S3 bucket reachable over trusted HTTPS from the executor. The
  fixture overwrites its disposable context's current version and verifies that
  the build still consumes the original signed version. Provisioning must allow
  `GetObjectVersion`; signing and executor requests must use the same reachable
  hostname. The profile's CA certificate is mounted separately from build inputs.
- Separate base-pull, candidate-push and runtime-pull Secrets. Base and push
  Secrets contain explicit Docker `auths`, in `.dockerconfigjson` or `config.json`.
  Conflicting credentials for the same registry key are rejected: provision
  compatible repository-scoped keys or separate registry endpoints. The runtime
  credential is never mounted in the builder. Rotate credentials by changing
  `provisioning_revision` and requalifying.

Helm provides namespace-scoped Job, log and Secret permissions for the backend
when RBAC creation is enabled. The build Pod has no service-account token. Site
registry ACLs enforce actual pull/push scope; the API allowlist alone is not a
registry authorization boundary.

## Recovery and retained evidence

The shared PostgreSQL operation queue journals each attempt before provider I/O.
Its unique `build-{operation_id}-{attempt}` Job name and registry tag never depend
on worker leases or polling counts. A Job is created suspended, its UID is saved,
then activation is fenced by the operation lock and Kubernetes resource version.
An uncertain create is recovered by exact name and ownership label. A changed UID
is never adopted or deleted; an already-recorded missing Job is a failure rather
than a rebuild under the same tag.

The executor receives a short-lived URL restricted to the recorded S3 object
version through an immutable per-attempt Secret. No ambient S3 credentials enter
the Pod. Input receipts retain archive, Dockerfile, dependency and base-image
digests alongside policy, executor, task and actor identities. An active attempt
pins context retention until cleanup completes.

Success requires a successful Job and executor exit, plus exactly one valid
`sha256` digest from the owned executor's termination message. No shared registry
tag lookup supplies the result. Each attempt retains its final digest and logs
in PostgreSQL before deleting the Job with a UID precondition and foreground
propagation, then deleting the per-attempt Secret. Jobs deliberately have no TTL:
an API/database outage must not erase uncollected evidence. The same worker
recovers cleanup after restart. Kubernetes enforces execution deadlines; the
backend also bounds admission/scheduling time to the execution timeout plus
120 seconds. Provider errors are retried without persisting raw exception text.

Logs retain up to 10,000 recent lines, capped at 1 MiB, with an explicit
truncation indicator. Known raw and
encoded credentials and URLs are redacted before returning or retaining logs.
Dockerfiles can print arbitrary data: do not put additional secrets in build
inputs. Cancellation retains the available log snapshot before stopping the Job.
Default log retention is 30 days; the existing retention worker expires logs in
bounded sweeps. Identity, context receipt and final digest remain after log expiry.

Release publication must call `repositories.builds.retain_for_release` in the
same transaction as its release record, before publication. It locks the attempt
against expiry and rejects missing/expired evidence. Referenced logs are retained
indefinitely; this change supplies the hook, not the downstream release publisher.

## Upgrade and verification

Startup additively creates `build_attempts` and `build_evidence_references` through
the existing serialized schema initializer. Existing operations and contexts are
unchanged. No data backfill is required. Keep executor access disabled until the
new fixture passes; changing transport to HTTPS requires fresh qualification.

Tests use Arrange–Act–Assert structure. `tests.test_builds` exercises API access,
recovery, cancellation, deadlines, retries, digest validation and retention.
`BUILD_TEST_DATABASE_URL` enables `tests.test_builds_postgres`, using a unique
schema per test for real concurrency, lease fencing and repeatable upgrade checks.
`BUILD_TEST_KUBERNETES=1` and `KUBERNETES_KUBECONFIG_PATH` enable
`tests.test_builds_kubernetes`, using disposable namespaces and a pinned tiny
executor fixture for actual Job, Pod, digest-message, log and deletion behavior.
These provider tests do not substitute for site-specific builder qualification.
Both integration suites are included in backend CI.

Verification on 2026-11-17: the full backend suite passed (1,410 tests, 272 opt-in
skips; 82.7% line/branch coverage). The build suite separately passed on real local
PostgreSQL (22 tests) and Kubernetes (3 tests). Seven focused provider unit tests
passed, including credential composition and controller-update races. Ruff, mypy
(336 source files), import-linter, affected Bandit checks, Helm lint and enabled-policy
rendering, OpenAPI export and the regenerated frontend client's TypeScript check
passed. The updated site-specific qualification fixture was not executed in this
session; execution remains gated on its reviewed passing receipt.
