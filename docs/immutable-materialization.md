# Immutable repository materialization

S12-A provides the shared repository initializer for validation and subsequent
release consumers. It does **not** enable production mode, approvals, the release
reconciler or a new deployment UI. Existing interactive development deployments
keep their current mutable S3 behavior. The validation executor (S07-B) and
release/rollout consumers (S09-F, S10-A, S12-B) must use this shared path rather
than reproducing the old S3 sync script.

## Contract and lifecycle

`app.schemas.materialization.MaterializationPlan` contains:

- The required registry digest of the materializer image; tags are rejected.
- Complete source and prepared inventories (relative path, byte size, SHA-256).
- One non-null S3 object version for every source file.
- A versioned normalization specification.

The storage endpoint, bucket, prefix and credential Secret are **local authorized
bindings**, not credentials supplied by a portable release. Provision credentials
with only `s3:ListBucket` restricted to the bound prefix and `s3:GetObjectVersion`
for that prefix. The materializer performs no S3 writes. Disable ambient instance
identity; only the init container receives these credentials. The runtime gets
neither the storage Secret nor a Kubernetes service-account token.

The init container lists the exact prefix, rejects missing/extra/unsafe keys,
and downloads the specified versions into a private staging directory. It checks
returned versions, sizes and hashes, applies the declared transform, and measures
the prepared inventory again. Nothing is published if a check fails. Once all
checks pass, it writes a receipt, flushes files/directories and atomically renames
the complete directory to `ready` on the same filesystem. The runtime mounts
`ready/models` read-only at `/models`. Source files and the receipt are outside
that inventory; the receipt is separately mounted read-only at
`/var/run/triton-materialization/receipt.json`.

A failed download leaves no finalized repository. An init retry removes abandoned
staging while holding an exclusive filesystem lock. An already finalized result
is rehashed and its receipt checked against the exact plan before reuse. Changed
plans/images cannot adopt an old result. A recreated Pod initializes a new
`emptyDir` from the same pinned versions and must produce the same prepared
inventory. This is filesystem recovery, not a database operation or migration.

Callers must supply an authorized `CaptureLimits` budget to both the renderer and
materializer. Byte/file ceilings and the timeout are required explicitly; there is
no hidden 10 GiB or 600-second runtime default. The renderer rejects over-budget
source/prepared inventories before creating resources and passes the exact limits
in `limits.json` to the init container. Consumers should derive the byte ceiling
and timeout from their effective installation policy, not from source metadata.
The volume size and init ephemeral-storage request are calculated as source bytes
plus prepared bytes plus 1 GiB metadata headroom. A 20 GiB source and 20 GiB prepared
repository therefore request about 41 GiB, rather than a fixed 32 GiB volume.

No archives, symlinks, hardlinks,
special files, arbitrary scripts or model code are accepted/executed by the
initializer. Model backend execution remains the serving runtime's responsibility.

## Deterministic normalization

`identity/v1` copies paths and bytes unchanged. Use it for already prepared
multi-model repositories imported by S05-B. `from_import(imported, helper_image)`
adapts an authorized versioned-S3 import receipt, excluding provenance metadata
from the model inventory. The caller resolves the binding to
`<import-prefix>/repository` and checks authorization independently.

`triton-layout/v1` optionally wraps an explicitly identified single-model root
containing `config.pbtxt` into a declared model directory. Optional rewrite rules
replace an exact quoted relative-path token in `config.pbtxt` or `model.json`
with a declared `/models/<inventoried target>` token. The expected occurrence count
must match. All other bytes, including whitespace and newlines, remain unchanged.
There is no heuristic discovery, environment expansion, tokenizer download or
production-only `sed` rewrite. Unsupported quoting/escaping must be prepared
explicitly upstream, or introduced through a new versioned transformation.

For example, the test fixture declares:

```json
{
  "version": "triton-layout/v1",
  "wrap_model": "iris",
  "rewrites": [
    {"file": "1/model.json", "relative": "weights", "target": "iris/1/weights", "occurrences": 1}
  ]
}
```

The offline command uses the **same** normalization implementation to measure
the expected prepared inventory before validation:

```bash
triton-materialize prepare --source ./captured-source \
  --normalization ./normalization.json --limits ./limits.json \
  --output ./prepared-repository > preparation.json
```

The limits document requires `max_bytes`, `max_file_bytes` and `deadline_seconds`.
For example, an authorized 40 GiB budget with a one-hour timeout:

```json
{"max_bytes": 42949672960, "max_file_bytes": 42949672960, "deadline_seconds": 3600}
```

Optional path/count/configuration limits retain the neutral capture contract's
bounded defaults. `run` also requires `--limits`; the Kubernetes renderer supplies
that document automatically. This is internal policy transport, not new environment
variables or Helm settings.

Supply a new output directory outside the source. On failure discard that offline
output; only `run` publishes atomically. Publish the captured source to versioned
storage, retain each returned VersionId and assemble the plan with the measured
inventories and the qualified helper digest. Do not replace a validation subject
with a newly calculated runtime hash: a mismatch must stop initialization.

JSON Schemas are exported to `contracts/materialization/v1`. They contain no
storage credentials or environment-specific default digest. The receipt binds
plan, source inventory, prepared inventory, normalization and helper image.
Canonical identity encoding is sorted-key compact UTF-8 JSON; inventory entries
are sorted by path, consistent with the existing neutral-import identity rules.

## Shared Kubernetes renderer

`app.services.materialization.kubernetes.manifests(release, plan, policy, binding,
name=..., namespace=..., limits=...)` renders an immutable plan ConfigMap and a Pod.
ConfigMap names include a hash of the full workload name, plan and limits. Long
names and changed plans do not collide with existing immutable ConfigMaps. It binds
the release's source/prepared inventory and helper identity to the plan and
requires matching qualification for the chosen Linux architecture. Both validation
and future production consumers call this same function. There is deliberately
no environment-specific normalization switch.

The serving image comes from the digest-pinned `ServingRelease`. Its command is
`tritonserver --model-repository=/models --model-control-mode=none
--strict-readiness=true`. There is no mutable polling, direct model-load control,
S3 sidecar, shell entrypoint or runtime package installer. Dependencies belong in
the qualified serving image. The Pod uses a non-root UID, dropped capabilities,
read-only root filesystem and a separate writable temporary volume.

This renderer does not authorize a release, fetch or approve evidence, select
per-model startup versions, provide rollout orchestration, or establish serving
readiness. Those remain downstream contracts. Consumers must authorize the target
and storage binding, verify validation/security subjects, provide image pull access
where required, and apply their separately qualified readiness/rollout behavior.
The qualification record is administrator-provisioned configuration, never a
self-approval supplied by an untrusted release author.

## Building and qualification

Build the helper from the repository root. Both the Python base and the resulting
helper must be resolved to actual registry digests:

```bash
docker build -f triton-backend/materializer/Dockerfile \
  --build-arg PYTHON_BASE_IMAGE="$PINNED_PYTHON_BASE" -t "$HELPER_TAG" .
docker push "$HELPER_TAG"
# Resolve the pushed registry manifest digest, not Docker's local image ID.
```

Dependencies install at image build time only. No default helper is enabled in
application settings or Helm. A consuming controller must explicitly provision
`MaterializerPolicy(image, platform, qualification)` and a read-only StorageBinding.
HTTPS is required by default; local test HTTP requires an explicit opt-in.

Qualify the **exact deployed digest** on each target architecture:

```bash
cd triton-backend
export MATERIALIZER_TEST_IMAGE="$PINNED_HELPER_IMAGE"
export MATERIALIZER_TEST_PLATFORM=linux/arm64  # or linux/amd64
export MATERIALIZER_TEST_KUBECONFIG=/path/to/test-kubeconfig
python scripts/qualify_materializer.py --output /path/to/evidence
```

This creates/deletes only its own test namespace. It runs unit failure/recovery
fixtures and actual init containers with a read-only S3 protocol fixture. The
actual pinned image is also rerun against the same volume without storage access,
against a corrupted retained file and against a changed helper declaration.
Interrupted-download recovery is exercised on that same volume. Each reported
check maps to successful named image tests; host unit tests alone cannot qualify it. A Python
probe replaces Triton to test byte/layout equality and an actual `EROFS` write
failure; **it does not claim inference correctness or model readiness**. Separate
MinIO integration tests exercise genuine S3 version retention and the neutral
import adapter. Qualification output is emitted only when all required tests
pass with none skipped. Fixture revision `materializer-qualification/v2` requires
renewed qualification; older records are rejected. The report's canonical JSON digest is referenced by
`qualification.json`; keep both in an immutable evidence store and review them
before provisioning the policy. Changes to image, architecture, transform version
or fixture policy require renewed qualification; old validation is not silently
rebased to new prepared bytes.

Backend CI builds the helper, pushes it to an isolated test registry, runs the
same Kubernetes qualification on Linux/amd64 and uploads its actual evidence.
The ephemeral CI image/fixture is not a published production image or human
approval. Requalify your retained registry image before use. The real S3 tests
run in the existing isolated PostgreSQL/MinIO job; no new application database
tables or upgrade migrations are introduced.

## Remaining integration work

This change supplies the shared primitive. S07-B and S12-B still need to wire it
into validation and production execution. The corresponding end-to-end acceptance
criterion in concept issue #55 remains open until both consumers are integrated.
