# Immutable build contexts (S06-B)

[Story #30](https://github.com/ai-lab-tech/triton-control-concepts/issues/30)
adds authenticated context upload and temporary, version-pinned S3 storage. It uses
S02-C durable operations, S05-B safe artifact paths/canonical hashing and S06-A
operator policy. It does not build images (S06-C / #31) or add a build button
(S06-D / #32). MLflow is not required.

## Installation

An administrator configures one existing S3 profile and a dedicated staging
prefix through the same `BuildPolicy` used for builder qualification. Users never
choose the destination bucket, endpoint, credentials or object keys.

With Helm, set `buildQualification.policy` to a complete policy described in
[build provisioning](build-provisioning.md). The chart copies the credential-free
policy into an application-namespace ConfigMap and mounts it read-only. It keeps
`policy.enabled: false`: uploading input is allowed, but executor admission stays
disabled until the S06-C execution/provisioning integration is installed and
qualified. Staging does not certify the builder or installed Kubernetes secrets.

For other installations, mount an operator-owned JSON policy file and configure:

| Setting | Meaning |
| --- | --- |
| `TRITON_BUILD_POLICY_FILE` | Path to that JSON file; no staging without explicit configuration |
| `TRITON_CONTROL_MODE` | Must be `development`; defaults to the existing development mode |
| `TRITON_BUILD_CONTEXT_RETENTION_DAYS` | 1–365 days, default 7; Helm: `buildQualification.contextRetentionDays` |

The selected profile must exist exactly once and the policy staging prefix must
be inside its prefix. The staging bucket must have versioning **Enabled**; the
application never enables it or creates buckets automatically. Grant scoped
PutObject, GetObjectVersion, ListBucketVersions and DeleteObjectVersion access
for the staging prefix, plus bucket-versioning inspection. Preserve the S3
profile binding across restarts; credentials may rotate but endpoint/bucket or
scope changes fail closed for already staged records. TLS verification and the
profile CA configuration are preserved.

Temporary raw and normalized files use private OS temporary storage. Allocate
sufficient ephemeral disk for concurrent uploads; they are not assumed to fit in
RAM. Both input tar and expanded regular-file bytes are bounded by the policy
`max_context_bytes` (default 10 MiB), as is the prepared gzip archive. This initial
API accepts uncompressed tar to enforce limits before extension/decompression
allocation. The policy limit therefore bounds upload bytes more strictly than
the original qualification fixture, which counts a compressed fixture context.

## API workflow

1. Build a complete uncompressed POSIX tar in the client, applying local selection
   before upload where possible. Supply root-relative names, without outside
   links. For example: `tar --format=ustar -cf context.tar Dockerfile requirements.txt app.py .dockerignore`.
   Only include `.dockerignore` if it exists. Root `./` directory prefixes are
   normalized; archive extensions such as PAX/GNU long-name records are rejected.
2. Calculate the exact SHA-256 and byte count of the input archive.
3. `POST /api/build-contexts` with an `Idempotency-Key` and this body (image digest
   must come from the real selected base, never substitute a fabricated digest):

   ```json
   {
     "schema_version": 1,
     "archive_digest": "sha256:<actual context.tar checksum>",
     "archive_bytes": 10240,
     "dockerfile": "Dockerfile",
     "allowed_paths": ["Dockerfile", ".dockerignore", "requirements.txt", "app.py"],
     "dependency_files": ["requirements.txt"],
     "base_images": ["registry.example/base@sha256:<actual base digest>"]
   }
   ```

   This illustrative body has placeholders and is not a runnable fixture. Actual
   byte counts and digests must match. `allowed_paths` contains exact files or
   directory prefixes; `.` explicitly selects the whole submitted context.
4. PUT the raw bytes to the returned `upload_url`, with
   `Content-Type: application/x-tar`. Upload and POST return HTTP 202 and a
   `Location` pointing to the shared operation status endpoint.
5. Poll `GET /api/build-contexts/{id}` or `GET /api/operations/{id}`. Only an
   operation in `succeeded` with `ready: true` is a currently retained, verified
   context. The receipt exposes the filtered file inventory, executable bits,
   Dockerfile checksum, selected dependency-file checksums, pinned external image
   identities and the normalized archive checksum/size.
6. Cancel with `POST /api/operations/{id}/cancel`. Failed staging can be retried
   with `POST /api/operations/{id}/retry`, then the same original archive can be
   uploaded again. Changed inputs require a new Idempotency-Key/context ID.

Only active installation engineers and admins may submit. The owner and current
admins may inspect/control a context, subject to current role and membership
checks. Other engineers cannot read another owner’s context through this API.
Delegated deployment tokens are rejected. Requests never grant access to arbitrary
workspace paths; the caller uploads selected bytes. Source workspace authorization
and collection for the future button remain S06-D responsibilities.

## Validation and identities

The body must match its declared archive size/hash. Upload reading has a 120-second
deadline, an exclusive five-minute fenced upload reservation and a 30-minute
initial/retry upload window. Expired idle upload windows reject new uploads even
when the worker has not yet marked the operation failed; use an explicit retry
after failure. Only one upload can own a context at a time. Failed
or disconnected body uploads release the reservation without publishing a context.
Idempotency is actor/kind/target/key scoped through the shared operation service.

The bounded tar reader never calls extract/extractall or follows links. It rejects
traversal, absolute paths, control characters, non-normalized paths, duplicate or
case-conflicting names, devices, links (including ignored links), sparse files,
archive extension records, truncation, trailing data and more than 10000 entries.

The Docker SDK matcher applies `.dockerignore` in order, including glob/negation
patterns and parent-directory matching. A selected Dockerfile-specific
`<dockerfile>.dockerignore` takes precedence over the root file. Patterns are
bounded to 1000 entries/256 characters each; control files are at most 64 KiB.
The selected Dockerfile and active ignore file must be in the explicit allowlist
and remain available in the prepared archive. Omitting the active ignore file is
rejected so the executor cannot fall back to different root ignore rules.
Missing/ignored dependency files or Dockerfile block publication.

A mandatory exclusion policy removes `.git`, `.aws`, `.ssh`, `.kube`, `.docker`,
`.azure`, `.config`, `.gnupg` trees, `.env`/`.env.*`, common credential files and
private-key/container credential formats. Ignore negations cannot reinclude these.
Selected bytes are also scanned for common private-key/AWS access-key signatures.
This is not a general secret detector: callers must not put arbitrary secrets in
source code or disguise them as ordinary files. Actual build credentials stay in
operator-controlled bindings and outside the uploaded context and receipt.

External FROM and COPY image inputs must match explicitly declared digest-pinned
images; stage references and `scratch` are supported. Dynamic FROM expressions,
custom Dockerfile syntax/escape directives and embedded credential ARG/ENV inputs
are rejected. Dependency-file identities are explicit selected inputs, not a claim
that every RUN download is pinned or that a build is fully reproducible.

The normalized archive sorts regular files, preserves executable intent, fixes
ownership/timestamps/modes and removes excluded bytes. Its inventory SHA-256 covers
sorted file paths/sizes/checksums/executable bits using S05-A canonical JSON. Every
upload attempt gets `<staging_prefix><context-id>/<attempt-id>/context.tar.gz`.
A source-archive checksum and prepared-archive checksum are distinct. The receipt,
S3 versions, operation states and approvals are outside the archive/inventory hash.

Before PUT, the database records the unique attempt key and intended manifest.
The durable worker verifies exact S3 version bytes before appending a receipt and
reporting success. After a crash or lost PUT acknowledgement, it observes that
attempt key and verifies the retained version instead of blindly writing again.
Versionless/null responses and missing/corrupt retained bytes cannot pass.

## Executor boundary, cancellation and retention

S06-C must call `consume_for_build` before starting each Job/retry. This hook
rechecks owner authorization, unchanged policy, current qualification and the
exact recorded S3 version/checksum. It extends retention to cover a Job admitted
immediately afterward, using policy timeout plus five minutes. The future executor
must download that **version**, verify it locally and use the receipt Dockerfile
path. The existing qualification-only renderer is not a general execution API and
must not be used to fetch a mutable latest key. S06-C must additionally verify
installed site credentials/policies before execution, as required by S06-A.

Cancel fences the uploader before deleting objects. An uncertain in-flight PUT
waits until its upload lease plus transport grace elapses before final cleanup.
Changing the live workspace cannot change the prepared archive. A successful
staging operation is not an image-build success, validation pass or release approval.

Retention starts at admission and is frozen per context (default seven days),
with the bounded executor-admission extension above. Every backend runs a sweep
of up to ten expired contexts every five minutes; PostgreSQL row locks serialize
replicas. It deletes every version and delete marker only under that context ID,
including abandoned attempts, and then marks the context expired. Partial failures
stay retryable. No registry images, original workspace files, candidate imports or
model-source objects are deleted. Receipts/operation history remain queryable;
`ready` becomes false at expiry even if deletion is still retrying.

An operator may additionally run `python scripts/cleanup_build_contexts.py` for a
bounded sweep of up to 100 contexts. Configure bucket lifecycle as a longer-term
fallback for abandoned temporary objects, not a shorter policy that can delete a
context still needed by an admitted build. In-flight filesystem scratch files are
removed on normal request completion; pod/node termination leaves their eventual
removal to ephemeral-storage lifecycle.

## Upgrade, rollback and verification

Startup additively creates `build_contexts` and `build_context_receipts`.
Append-only receipt guards use the existing serialized schema migration path;
existing operations, tasks, profiles and workloads are preserved. Context IDs
refer to durable operation IDs without a deletion-cascading FK, preserving audit
records independently of future operation-retention policy. Application DB users
need the existing trigger/function provisioning privileges.

Rolling back stops staging/cleanup workers and preserves rows and S3 versions;
retain the original storage profile for later recovery. Never automatically adopt
old mutable workspace paths or invent base-image hashes during migration.

Relevant suites: `tests.test_build_contexts`, `tests.test_build_contexts_settings`,
`tests.test_build_contexts_postgres`, `tests.test_build_contexts_s3`. PostgreSQL uses
`BUILD_CONTEXT_TEST_DATABASE_URL`; real MinIO uses `BUILD_CONTEXT_TEST_S3_ENDPOINT`,
`BUILD_CONTEXT_TEST_S3_ACCESS_KEY`, `BUILD_CONTEXT_TEST_S3_SECRET_KEY`. Each test uses
an isolated schema/bucket and removes only its fixtures. Backend CI runs both real
providers in the existing candidate-import provider job. No Kubernetes workload
provider is added in this increment; Job execution tests belong to S06-C.

Local validation (2026-11-16): full backend discovery ran 1209 tests successfully
with 170 opt-in environment tests skipped, including the enabled real PostgreSQL
and MinIO build-context suites; total coverage 83.4%. The focused suites ran 51
tests successfully. Ruff, mypy (303 files), all four architecture contracts and
Bandit passed. Helm lint and a configured render verified policy mount, namespace
and retention settings. No image-build or production workload was started.
