# Prepared Triton repository import

S05-B adds a neutral backend service for turning an already prepared Triton
repository into a captured, inventoried candidate. It uses S05-A's version-one
identity contracts and does not require MLflow, a generator or a running Triton
server. It never converts weights, rewrites configuration or executes model code.
Structural import success is not a correctness/performance result or production
approval.

## Entry points and integration boundary

`app.services.candidates.importer.import_repository` is the internal entry point.
A future authorized handoff, snapshot or workspace service constructs exactly one
source provider and selects an administrator-controlled artifact store. It passes
a `PreparedImportRequest`, the authenticated claims and its database session.
`get_import` reads a retained result after current account/task authorization and
verifies the retained bytes.

No new HTTP endpoint, UI action, production deployment or background worker is
introduced by this story. In particular, request bodies cannot select arbitrary
server filesystem paths, endpoints, buckets, credentials or multiple source URIs.
The downstream integration must authorize a local workspace directory before
constructing `DirectorySource`, establish the local installation identity, and
verify provenance references obtained from other tools. The importer checks
content and lineage consistency; client-declared ancestry alone is not proof of
training provenance.

Source adapters:

- `DirectorySource(root)`: one authorized local directory, captured with anchored
  directory descriptors, no-follow opens and before/after checks. This adapter is
  Linux/POSIX-specific, matching the backend container.
- `S3Source(client, bucket, prefix)`: one authorized prefix. Versioned objects are
  read by version ID; unversioned/null-version objects use conditional ETag reads.
  The adapter computes its own SHA-256 hashes and rechecks the listing and current
  object identities to reject source changes during capture. An ETag is never
  treated as the model content digest.
- `source_from_profile(session, claims, profile_id, relative_prefix)` constructs
  the S3 adapter from existing profile visibility/token rules, TLS configuration
  and bounded network timeouts. The relative prefix cannot escape the profile's
  configured prefix. This grants no additional source access through team/task
  membership.

Artifact stores are selected by trusted application configuration/integration,
not by the model's files or an import payload:

- `DirectoryArtifactStore(root)`: private persistent storage, distinct from the
  source directory. All backend replicas must share it. Files/directories are
  flushed, the snapshot is atomically renamed and sealed read-only. An ephemeral
  container filesystem is suitable only for disposable development/testing.
- `S3ArtifactStore(client, bucket, prefix)`: a versioning-enabled destination.
  Every file and manifest receipt retains its exact object version. Later writes
  to the same key cannot silently substitute those bytes. Current single-object
  writes support files up to 5 GiB; multipart upload is not claimed.
- `store_from_profile(...)` is an admin-only setup helper, including a current DB
  role check. Source and destination may share a bucket only with disjoint
  prefixes. S3 clients constructed manually must also have bounded timeouts.

For example, an application integration can resolve an existing source profile,
select a configured store and invoke:

```python
from app.schemas.candidate_import import PreparedImportRequest
from app.schemas.lineage import Origin
from app.services.candidates.importer import import_repository
from app.services.candidates.profiles import source_from_profile

# claims, session, configured_store and stable_installation_id are provided by
# the authenticated application; identifiers refer to the actual local operation.
source = source_from_profile(session, claims, profile_id, "triton/prepared")
request = PreparedImportRequest(
    candidate_origin=Origin(installation_id=stable_installation_id, record_id=candidate_id),
    capture_origin=Origin(installation_id=stable_installation_id, record_id=capture_operation_id),
    manual_source_origin=Origin(installation_id=stable_installation_id, record_id=manual_source_id),
    triton_version="26.06",
)
result = import_repository(session, claims, request, source, configured_store)
```

The illustrative IDs above must be supplied by the actual capture/handoff flow;
they are not fake MLflow runs. Do not expose this internal example unchanged as an
endpoint that accepts unverified source/installation identities from clients.

## What is validated and preserved

The importer first copies into a private temporary directory. It rejects unsafe,
non-NFC/absolute/traversing paths, duplicate/colliding S3 keys, symlinks (including
parent-directory replacements), hard links, FIFOs and other special files.
Local sources should be quiescent; detected concurrent edits reject capture.
The resulting staged bytes are the snapshot, not a pointer to a moving source.
An administrator-controlled hostile filesystem/storage account remains outside
this application boundary.

Model directories must have `config.pbtxt` and nonempty, positive canonical numeric
version directories. Configurations are parsed against an explicitly supported,
locally bundled Triton protobuf schema. Model names, backend/platform agreement,
expected backends, specific version selection and known/default model filenames
are checked. Unknown fields or unsupported Triton schema versions fail. Independent
models within one repository are supported; ensembles/repository agents and
multi-source transformation DAGs need a future contract. Empty-version-only layouts
are not supported by this initial file-inventory importer. Backend availability
and actual model loading remain runtime validation responsibilities.

Root `requirements.txt`, `Dockerfile`, `serving.json`, `README.md` and `.gitignore`
are retained and hashed as artifacts. Other root-level artifacts are rejected
unless they are the reserved metadata sidecars below. Build hints are not executed.

Each model artifact gets a normalized relative path, measured byte size and SHA-256
hash. The overall inventory uses the existing canonical UTF-8 sorting/hash rules
from S05-A. Unchanged copies have identical inventories; changed weights,
configuration or dependencies change the inventory. Source bytes are never altered.

Root `lineage.json`, `inventory.json`, `receipt.json`, `import-receipt.json`,
`validation.json` and files under `.triton-control/` are copied byte-for-byte to a
separate metadata area and separately inventoried. They are excluded from the
model inventory to avoid receipt/self-hash cycles. The same filename inside a
model version is a runtime artifact and remains hashed. Source-provided metadata
is retained, not trusted as authorization or approval evidence.

Existing source snapshot origins, plugin references and actual engineering attempts
are preserved. Their inventory chain must match the measured prepared output;
every referenced earlier inventory must be supplied. The actual capture appends
one `packaging` attempt with unchanged input/output content. It never invents a
training or conversion run. A manual repository supplies a manual source origin
and needs no MLflow IDs. Engineering predecessor and source ancestry remain
separate relationships; production release replacement is not an import relation.

## Storage and completeness

PostgreSQL `serving_candidates` retains the canonical S05-A envelope and referenced
inventories. The new insert-only `candidate_imports` table retains the candidate
and import digests, full result, source transport receipt, storage references,
actor/task and creation time. `result.inventory` contains the per-file checksums;
`candidate.output_inventory` is the canonical inventory digest. Shared JSON Schemas
and golden fixtures live under `contracts/candidate-import/v1` and
`triton-backend/tests/fixtures/candidate_import`.

The artifact store contains:

```text
<snapshot>/
  repository/       # exact model/runtime artifact bytes
  source-metadata/  # exact excluded provenance/receipt sidecars
  manifest.json     # candidate, inventories, source receipt and missing inputs
```

The manifest is written after its artifacts and hashed separately; its own digest
and publication receipts are not inserted into the model inventory. S3 references
pin every object version, including the manifest. Directory snapshots use opaque
IDs under the configured root. Credentials are neither serialized into manifests
nor returned in results; provider errors do not return upstream credential detail.

By default the result lists missing evaluation dataset, expected results and
acceptance thresholds. When linked to a task, its missing inputs are retained too.
An empty missing-input list means `evaluation_inputs_declared`, not verified test
success or release readiness. Changing retained metadata/completeness requires a
new import origin; an existing immutable receipt is not silently updated.

## Authorization, transactions and recovery

Every use resolves the current active DB account. Standalone import requires admin
or current installation engineering eligibility. The installation engineering
group must already be initialized through S02-A. A task-linked import additionally
requires the current assignee (or admin), a non-cancelled task and its current
expected revision. Task membership does not grant access to its underlying source.
Reads of standalone captures require their owner or admin; task-linked reads
recheck current assignment. Admins can inspect retained captures after task cancellation; cancellation blocks further imports. Revocation never deletes retained provenance.

All capture/storage I/O precedes the final task/team row locks. Authorization and
revision are rechecked before atomically publishing the candidate and import
receipt. Same-origin concurrent imports produce one immutable database winner;
identical repeats return its retained result after byte verification, conflicting
content returns a conflict. Capturing files does not change task/build/deployment
state or imply a production approval.

Default publication commits both rows. `commit=False` joins a caller-owned
transaction; it must commit or roll back. Source/storage failure, revocation or DB
rollback does not publish a half-complete candidate. Completed but unregistered
snapshots can remain after a race, a DB failure, a lost write response or a caller
rollback. They are private orphans, not candidates or approvals. Retain them for
reconciliation; do not delete data based solely on a timeout. This story does not
claim distributed exactly-once storage writes or implement automatic garbage
collection. Registered snapshots must be protected by storage retention policy;
missing/corrupt bytes block verified reads instead of rewriting identity.

Default per-capture limits: 10,000 files, 10 GiB total/per file, depth 32, 1 MiB model
configs and a 120-second deadline checked between files/chunks. Manifests are capped
at 64 MiB; S3 clients additionally use connection/read timeouts. Temporary capture
directories are removed on success/failure. Local directory enumeration checks the
path-count budget and deadline before buffering names for sorting, including the
final source-consistency scan. Provider parameters/limits come from
trusted application setup, not unvalidated model metadata.

## Upgrade and verification

Serialized startup additively creates `candidate_imports` and its index/append-only
triggers. Existing candidates, tasks, deployments, profiles and legacy MLflow
fields remain unchanged. No source is imported automatically on upgrade. The
existing schema-owning DB account needs function/trigger permissions. Keeping the
new table/artifacts during application rollback preserves audit data; destructive
schema/storage removal requires explicit retention review.

Run unit and real-provider suites from `triton-backend`:

```bash
python -m unittest tests.test_candidate_import tests.test_candidate_profiles tests.test_candidate_s3 -v
CANDIDATE_IMPORT_TEST_DATABASE_URL=postgresql://test:test@localhost:5432/test \
  python -m unittest tests.test_candidate_import_postgres -v
CANDIDATE_IMPORT_TEST_S3_ENDPOINT=http://127.0.0.1:9000 \
  CANDIDATE_IMPORT_TEST_S3_ACCESS_KEY=candidate_test \
  CANDIDATE_IMPORT_TEST_S3_SECRET_KEY=candidate_test_password \
  python -m unittest tests.test_candidate_import_s3_integration -v
python scripts/export_candidate_import_schema.py
```

PostgreSQL tests isolate random schemas; S3 tests isolate random buckets and remove
all test versions. Use disposable test services. The CI job builds its test-only
MinIO server from pinned upstream commit
`07c3a429bfed433e49018cb0f78a52145d4bedeb` and runs the real PostgreSQL/S3 suites.
No Kubernetes workload is created by this story, so Kubernetes execution testing
is not applicable. Structural ONNX fixture bytes are explicitly non-executable;
these tests do not claim model-runtime qualification.

### Local implementation evidence (2026-11-15)

The final full backend run enabled the real PostgreSQL import/lineage and MinIO
suites: 1,025 tests completed successfully, with 77 unrelated environment-dependent
tests skipped. Overall coverage was 82.7%; importer modules were 89.7% covered.
This included same-origin concurrent publication, conflicting bytes, rollback and
retry, version-pinned S3 reads after overwrite, detected source changes, missing
retained versions, cancellation audit reads, and a symlink replacement between
stat and open.

Strict mypy passed for 269 files; Ruff, all four architecture contracts and Bandit
passed. Shared importer schemas were regenerated and golden fixtures checked.
OpenAPI export remained unchanged because this story adds internal services rather
than HTTP routes. The new CI workflow itself will run after the branch is pushed.
