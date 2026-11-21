# Serving metadata v1alpha1

Serving metadata separates immutable release content, target capacity and
metadata-source access.
The schemas are in [`contracts/serving/v1alpha1`](../contracts/serving/v1alpha1).
They are generated from pure models in `app/schemas/serving_metadata.py`; identical
copies ship inside the backend wheel. JSON and YAML use the same schema and
semantic validator. There is no production registration or execution API yet.

## Validate locally or in CI

From `triton-backend`, after installing `.[dev]`:

```sh
python scripts/validate_serving_metadata.py release.yaml target.json source.yaml
python scripts/validate_serving_metadata.py --format yaml - < release.yaml
python scripts/export_serving_metadata_schema.py --check
python scripts/generate_serving_metadata_fixtures.py --check
python -m unittest discover -s tests -p test_serving_metadata.py -v
```

An installed wheel also provides `triton-validate-metadata` and
`python -m app.services.serving_metadata_cli`. They work outside the checkout.
Each valid input prints its zero-based input index, kind, version and canonical
digest. Any invalid or unreadable input causes exit status 1; batch processing
continues. Errors omit filenames, document values and unknown keys to avoid
echoing credentials. `--format json` enforces JSON grammar; auto mode recognizes
`.json` filenames and JSON-looking byte input. Use `--format yaml` for YAML flow
syntax starting with `{` that is not JSON.

Service callers use `load_metadata(raw_bytes, format="auto")` from
`app/services/serving_metadata.py`. The result contains a typed document, canonical
UTF-8 bytes and digest. Validation performs no network access and never resolves
credentials, executes manifests or checks report authenticity. Do not use direct
model construction as the untrusted-document entry point.

## Wire contracts

Every document explicitly contains `apiVersion: serving.triton-control/v1alpha1`,
`kind`, `metadata` and `spec`. All objects reject additional properties. Names and
logical credentials are lowercase alphanumeric/hyphen identifiers up to 63
characters, starting/ending alphanumerically. Schemas specify all required fields,
length bounds and allowed values.

| Kind | Required payload |
| --- | --- |
| `ServingRelease` | `metadata.name` and publisher label `metadata.release`; pinned `spec.image`; `repository` URI, source `inventoryDigest`, `preparedInventoryDigest` and optional pinned `materializerImage`; `models` with explicit versions; `resourcesPerReplica`; `lineage`, `lineageManifest` reference; `validation` evidence reference. `security` is optional. |
| `ServingTarget` | `metadata.name` is the target ID. `spec.release` selects a URI and exact release digest. `replicas` is a positive integer; `capacityOwner` defaults to `source`. `credentials.modelStorage` and `.imageRegistry` are local logical bindings. `rollout` contains integer `maxSurge` and `maxUnavailable`. |
| `MetadataSource` | `metadata.name` is the stable source ID. `spec.location` discriminates Git (`type`, HTTPS `repository`, `ref`, relative `path`) from S3 (`type`, `bucket`, relative `key`). `readCredential` is separate from target bindings. `allowedTargets` is a nonempty explicit list. |

Sources default to `intervalSeconds: 86400`, `approval: {mode: manual}` and
`enabled: false`. Intervals range from 1 to 2592000 seconds. The alternative mode
`independently-approved` allows a later executor to act only after verifying
independent approval; it does not approve a release. No automatic source is
enabled by installation. Source IDs remain stable across configuration revisions;
the configuration hash is a separate identity. Source ownership/config-vs-database
collision checks belong to source configuration, not this offline validator.

Repository URIs use S3 or HTTPS. Reference URIs may also be relative paths resolved
against a future fetched source snapshot. Relative paths use ASCII alphanumeric,
dot, underscore, hyphen and slash; `.`/`..`, empty components, absolute paths and
backslashes are rejected. URIs reject user info, queries, fragments, percent
encoding and unsafe characters. Git uses HTTPS only in this initial version;
read authentication is supplied by the separate local binding. Local policy must
still authorize hosts, buckets, targets and bindings before any future fetch.

This contract intentionally excludes arbitrary Kubernetes Secrets, profile IDs,
Vault paths, inline credentials, template commands and Kubernetes workload specs.
Do not place secrets in otherwise valid identifier strings either; a validator
cannot prove that an arbitrary string is not confidential.

## Semantic checks beyond JSON Schema

Other-language consumers must apply these checks as well as the shared schemas:

- Startup model names are unique; each has unique positive numeric version strings.
- CPU is a positive core decimal (at most three fractional digits) or integer
  millicores. Memory is a positive integer byte count with optional k/M/G/T or
  Ki/Mi/Gi/Ti suffix. Requests cannot exceed limits; compare exact quantities.
  GPU count is a nonnegative integer. Counts are bounded at 1000000, but this
  structural ceiling grants no capacity: quotas and supported hardware are local
  binding and rollout admission decisions.
- A changed prepared inventory requires a digest-pinned materializer. If one is
  supplied even for identical inventories, its digest must be bound by evidence.
- `lineage` is the existing `ReleaseLineageV1` relationship object, retaining its
  snake_case fields and origin/candidate/replacement meanings. Its runtime image
  must match `spec.image`, and it cannot replace its own origin. `lineageManifest`
  identifies the separately archived provenance document; the offline validator
  does not dereference it or prove it matches the submitted relationships.
- Evidence subjects must match the selected image digest, source/prepared inventory
  digests and materializer digest (or null when absent). Optional source-code and
  validation-input digests are explicit fields. Required validation evidence and
  optional security evidence follow the same reference shape: `uri`, `digest`,
  `subjects`. [Validation contracts](validation-contracts.md) define reports, envelopes and trust boundaries.
- Target surge and unavailability cannot both be zero. `maxUnavailable` cannot
  exceed replicas. Booleans, fractions, percentage rollout values and string
  replicas are rejected. Target IDs in a source must be unique and cannot be wildcards.
- URI/path and Git-ref validation is mandatory beyond structural regex checks.
  This reader rejects traversal and credential-bearing locations without fetching.

## Content identities and evidence ordering

Canonical bytes use the lineage convention: JSON in UTF-8, sorted object keys, compact
separators, no ASCII escaping, no floating-point/nonfinite values. Model defaults
and null fields are included. Arrays preserve order, and ordering is significant.
The SHA-256 digest hashes the complete normalized document. Whitespace, key order
and equivalent YAML/JSON syntax do not change identity. Semantically equivalent
resource spellings such as `1` and `1000m` remain different content.

The computed release ID lives **outside** its payload. `metadata.release` is a
publisher label, not a self-hash. Any content edit produces a new computed ID.
Release publication must reject attempts to overwrite an existing publisher label
or lineage release origin with different content. The offline contract adds no mutable
release storage and does not claim database immutability enforcement.

Compute artifact inventories and lineage first, evidence references second,
release digest third and target digest last. Evidence identifies artifact subjects,
never the final release document that includes its own evidence digest. A later
detached signature can bind the completed release. Receipts, approvals and observed
Git commits/S3 versions stay outside these hashed payloads. No ETag, timestamp,
`passed: true` or inline approval can substitute for digest/trust verification.

Scaling or changing a target-local credential changes the target hash, not the
selected release hash. A runtime-only release keeps the same candidate digest and
selects a new pinned image with matching evidence. Candidate source ancestry,
previous serving candidate and replaced production release remain distinct.

## Safe loading

Before object construction, the event parser enforces at most 1 MiB input,
32 nested node levels and 10000 nodes (mapping keys count as nodes). CLI reads at
most limit+1 bytes. Only one UTF-8 mapping document is accepted. Duplicate keys,
anchors, aliases, merge keys, explicit YAML tags, non-string keys and oversized
integer scalars are rejected. JSON must also pass JSON grammar validation.
YAML uses JSON-like scalar resolution (`true`, `false`, `null`, decimal integers);
legacy booleans and dates remain strings. Floating numbers are not contract values.
Unsafe parser errors are replaced with stable diagnostic codes.

## Fixtures and verification

[`fixtures`](../contracts/serving/v1alpha1/fixtures) contains paired JSON/YAML,
normalized bytes and fixed hash vectors. All artifacts are explicitly synthetic
and non-deployable. Image descriptor hashes are calculated from committed test
bytes; they are not claims about real registry images. Evidence is synthetic test
data, not signed qualification or approval. No real credentials are present.

Tests cover manual/no-plugin lineage, runtime-only releases, optional security,
Git/S3 source defaults, scale-only changes, subject mismatches, loader attacks,
schema/package drift, safe CLI failures and preservation of lineage v1 vectors.
CI also builds a wheel and invokes both CLI entry points outside the checkout.
This increment performs no database publication, concurrency or Kubernetes
operations, so it introduces no provider behavior requiring those integration tests.

Local verification on 2026-11-16 (Python 3.14): 1087 backend tests ran successfully,
with 130 opt-in integration tests skipped; overall coverage was 82.6% (75% gate).
All 21 serving-contract tests passed with Pydantic 2.13.4 and 2.14.3. Full mypy
passed for 283 files, ruff passed for backend and new scripts/tests, all four
import-linter contracts passed, and Bandit found no issues in the new modules.
The wheel was built and its installed CLI used outside the checkout. Sandbox
restrictions stalled an existing async test; the full suite passed outside that
sandbox. CI repeats the contract and installed-wheel checks on Python 3.12.

## Migration and downstream responsibilities

This addition does not alter existing deployment APIs, legacy MLflow fields,
candidate/lineage v1 identities, tables or running workloads. Legacy inputs cannot
be silently converted: missing immutable digests, capture lineage and verified
evidence must be obtained explicitly. There is no automatic production adoption.

New contract versions require a new discriminator/schema directory. Readers reject
unknown versions. A future explicit conversion preserves the original document,
creates a new identity and requires renewed downstream review. Rollback of this
tooling does not change deployments; preserve published schemas for consumers.

The contracts reuse the [lineage identities](lineage-contract.md) and preserve
these boundaries between components:

| Component | Boundary preserved |
| --- | --- |
| [Validation contracts](validation-contracts.md) | Artifact-subject references are acyclic; issuer, policy, dataset/evaluator and report authenticity remain the evidence contract's responsibility. |
| [Local bindings](local-bindings.md) | Source-read, storage and pull bindings are distinct logical names; authorization, quotas and resolution remain local. Build-push credentials do not belong in serving metadata. |
| Source configuration | Stable IDs, explicit target allowlists and disabled/manual defaults support source registration and independently reviewed ownership. |
| Release publication | Frozen identities, provenance references and external receipts support artifacts/evidence → release → target publication ordering and conflict checks. |
| Production approval | Target/release hashes are available for review; no metadata field substitutes for independent human approval or audit. |
| [Immutable materialization](immutable-materialization.md) | Source/prepared inventories and pinned materializer identities are explicit; no fabricated helper digest/default or runtime rewriting is introduced. |

Local authorization and credential resolution are implemented by [local
bindings](local-bindings.md).
