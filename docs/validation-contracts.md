# Validation contracts v1alpha1

The validation contracts define what is tested and what a result means. They supply
schemas, a typed evaluator interface, deterministic identities and offline
consistency checks. They do not execute an evaluator, create Kubernetes workloads,
authenticate an issuer, grant release readiness or implement production mode.

For the development Job, provisioning and status API, see [Execute an exact-candidate validation](candidate-validation.md).

## Three documents

All documents require `apiVersion: validation.triton-control/v1alpha1`, an explicit
`kind`, and reject unknown fields/versions. Shared JSON Schemas are published under
`contracts/validation/v1alpha1` and packaged with the backend. Use the loader plus
semantic checks, not JSON Schema alone, at an untrusted boundary.

| Document | Bound content |
| --- | --- |
| `ValidationManifest` | Serving image digest; source/prepared inventories and pinned materializer; optional source-code reference; exact dataset and expected-output references; evaluator image, code hash and version; acceptance criteria; startup models/configuration and per-replica resources; explicit supported hardware/runtime profiles. |
| `ValidationReport` | Full manifest hash; evaluator identity; artifact subjects; measured hardware/runtime; start/end timestamps; measured metrics with thresholds and pass flags; status and bounded error codes. |
| `EvidenceEnvelope` | Issuer identifier; policy name/version; tool versions; artifact/manifest subjects; report URI/hash; issue/expiry timestamps. |

A manifest must contain at least one correctness criterion. Performance checks alone
cannot establish model correctness. Metrics have a name, category, unit, operator
(`gte`, `lte`, `eq`) and threshold. Measured values and thresholds are finite decimal
**strings**, e.g. `"0.95"` and `"12.5"`, so hashing and comparisons do not depend on
floating-point serialization. Numeric JSON values, NaN and exponent syntax are
rejected. Ordering and exact decimal spellings are significant in v1alpha1.

`StartupConfiguration.configuration` identifies the immutable serving configuration
artifact. The validation runner must verify and use that artifact, the declared startup
models/resources, all source bytes and the pinned evaluator; a schema cannot prove
that these are what a workload actually ran. The measured runtime profile is reported
by that runner. Source references contain URIs/hashes, never inline dataset contents,
credentials, commands or Python code to execute in the Control API.

## Evaluator interface and outcomes

`app.schemas.validation.Evaluator` is a Python protocol:

```python
def evaluate(manifest: ValidationManifest) -> ValidationReport: ...
```

The validation runner implements this interface in an isolated owned runner. It writes
the returned
report using `report.model_dump_json(by_alias=True)` as `report.json`. Other-language
evaluators use the same JSON contracts. The protocol introduces no API-process
execution hook. The validation controller reads reports from its owned evaluator pod.

- `passed`: every required metric is present exactly once and passes its criterion.
- `failed`: all required metrics are present and at least one misses its threshold.
- `error`: explicit runner/timeout/cancellation/artifact/evaluation/missing-metric
  error; partial metrics are allowed, but never confer readiness. If the runner
  failed before observing hardware, `measuredTarget: null` and empty metrics record
  that gap without inventing an environment.
- `unsupported`: explicit unsupported-target error with the observed profile;
  cannot be treated as a pass.

The report model checks each pass flag against its measured value. `check_report`
also checks the manifest hash, evaluator, artifact subjects, complete criterion set,
units/thresholds and supported measured profile. Thus a client-supplied `passed`
flag or a report containing only its successful checks is insufficient. Timestamps
must be timezone-aware RFC 3339 strings and ordered; they normalize to UTC.
Numeric epoch strings and timestamps outside the representable UTC range are
rejected with safe validation errors. Diagnostics are bounded
error codes and optional digest references to separately protected logs, not raw
provider exception text.

## Hardware, scaling and evidence validity

Supported targets are explicit **exact profiles** in this first contract: CPU
architecture/model, Linux, GPU model/count, driver/CUDA versions and Triton version.
CPU-only profiles require GPU/driver/CUDA fields to be null. GPU profiles require
all of them, and their count must match per-replica resources. Version ranges and
implicit architecture/GPU compatibility are deliberately not inferred. Supporting
additional profiles changes the manifest and requires new evidence. Declaring a
profile supported does not establish measured performance: evidence can be used
only for the exact `measuredTarget` in its report. Each additional profile needs
its own report and evidence envelope.

`check_evidence_consistency(manifest, report, envelope, target=..., policy=..., now=...)`
checks passing report consistency, envelope/report identities, expected policy and
evaluator tool version, supported target and validity window. The caller supplies
its expected policy and a timezone-aware current time. This function is a necessary
consistency check only: a forged but internally consistent envelope can still pass.
**Evidence admission must authenticate the issuer, apply trusted policy and revocation
checks.**
Separate human deployment approval remains outside all three documents.

Replicas are not part of the validation manifest. Both manifest and report specify
`performanceScope: single-replica`. Changing only `ServingTarget.spec.replicas`
changes the target hash while retaining artifact validation. This makes no fleet
throughput/latency guarantee and does not skip capacity policy, target admission,
rollout checks or independent approval. A different image, model inventory,
materializer, dataset, expected outputs, evaluator, thresholds, startup configuration,
resources or supported profile changes the manifest hash and rejects the old report.
Existing reports are retained by their identities rather than relabelled as current.

## Identities and ServingRelease integration

Canonical bytes follow the lineage and serving metadata conventions: sorted object keys,
compact UTF-8 JSON, explicit
model defaults/nulls, stable array order, no floating-point values. `document_digest`
hashes the complete normalized document. Semantically equivalent YAML/JSON documents
have the same identity. Reference hashes for these contract documents refer to their
**normalized canonical bytes**; ordinary artifact references hash raw artifact bytes.
A later publisher must preserve canonical archived report/envelope bytes accordingly.

The dependency order is acyclic:

1. Artifact inventories, dataset, expected outputs, evaluator and configuration.
2. Validation manifest hash.
3. Report hash, binding `manifestDigest` and artifact `subjects`.
4. Envelope hash, binding the report and `subjects`.
5. ServingRelease hash, binding the envelope through `spec.validation`.
6. ServingTarget hash, selecting the release.

`ArtifactSubjects` is reused from serving metadata. Its `validationInput` contains the **complete
manifest hash**, which binds dataset/evaluator/criteria/startup/hardware rather than
only the dataset. Its optional `sourceCode` binds the declared source-code artifact.
`check_release_binding` compares the release image, repository, startup models,
per-replica resources, subjects and envelope digest with the validation inputs.
It does not fetch references or verify their bytes. Validation execution and release
publication retain and verify
artifacts; evidence admission handles trust. Neither manifest nor report/envelope embeds
a final
release hash or approval. Detached authentication can cover the completed envelope
without introducing a self-hash.

## Offline loading and CLI

The validation loader reuses the bounded serving metadata JSON/YAML event parser: 1 MiB,
32 levels and 10000 nodes before constructing a document. Multiple documents,
duplicate keys, unsafe tags, anchors/aliases, invalid paths/credential-bearing URIs,
unknown versions and fields fail closed. No network access occurs. Error messages
do not echo input values, unknown keys, credentials or local filenames.

```sh
cd triton-backend
python scripts/export_validation_schema.py --check
python scripts/generate_validation_fixtures.py --check
python -m app.services.validation_cli ../contracts/validation/v1alpha1/fixtures/manifest.json
python -m unittest tests.test_validation_contracts tests.test_serving_metadata tests.test_lineage -v
```

Installed wheels provide `triton-validate-evidence FILE...` as well as the module
entrypoint. JSON and YAML use identical schemas and semantic rules. Exit status is
nonzero if any document fails; CLI output explicitly says
`structure-and-semantics-only`. To check a bundle, use the cross-document service
functions above in addition to loading its documents. A successful CLI invocation
is not an authenticated quality gate result.

## Fixtures, migration and downstream scope

The fixtures include a complete manifest, passing/failing/unsupported reports,
an envelope, a bound serving release and scale-only target variants. All artifacts,
image descriptors, metrics, issuers and timestamps are **synthetic, non-deployable**
test data; hashes are derived from committed bytes. No real benchmark, image digest,
signature or approval is invented. Tests mutate inputs individually, check missing
metrics and tampering, unsupported hardware, policy/expiry, scale semantics,
canonical identity, hostile parsing and schema/fixture drift.

Existing deployments, databases, candidate/lineage v1 and serving v1alpha1 documents
are unchanged. No automatic conversion of old perf-analyzer results is provided:
missing dataset/evaluator/criterion/hardware provenance cannot be inferred. Future
versions need a new discriminator/schema directory; preserve original documents
and identities, explicitly convert and revalidate. New schemas alone do not update
running workloads or mark any release ready.

These contracts introduce no database, concurrency, network provider or Kubernetes
behavior. Validation execution has separate worker, PostgreSQL and Kubernetes tests.
CI checks contract compatibility with lineage and serving metadata, and tests the
packaged-wheel CLI outside the checkout. Production execution, trusted evidence
admission and security gates remain separate integration work.

## Implementation verification

Local verification on 2026-11-16 (Python 3.12, Pydantic 2.14.3):

- Full backend discovery: 1176 tests ran, 170 environment-dependent integration
  tests skipped, no failures. After the final unmeasured-runner-error addition,
  all 47 validation/serving/lineage contract tests passed again.
- Backend coverage: 83.2%, above the existing 75% CI gate.
- Full mypy: 298 files passed. CI-scoped Ruff (`app/ main.py`) plus all new
  scripts/tests passed; four architecture contracts and new-module Bandit passed.
- Schema/fixture drift checks passed. The wheel was built and the module and
  installed console entrypoints ran outside the checkout.

An additional Ruff scan of all pre-existing tests/scripts reports 53 findings in
unchanged files; these are outside the current CI Ruff scope and this change.
Remote CI and implementation PR evidence remain pending publication of the branch.

## Trusted validation evidence

Set `TRITON_GATE_POLICY_FILE` to an operator-owned JSON file mounted read-only
for the backend. The GatePolicy document contains `identity` (name and version),
`issuers` (issuer names mapped to lowercase hex Ed25519 public keys),
`evaluator` (the pinned evaluator identity), `requiredCriteria` (exact metric,
category, unit, operator, and threshold entries), and `maxValiditySeconds`.
The release publisher must have no write access to this file or to issuer keys.

Before signing, the independent issuer must verify the exact validation result
and check the manifest's dataset and expected-output digests against artifacts
approved independently of the release publisher. Verify that the bytes used by
the evaluator match those approved digests. Sign the bytes returned by
`signing_message` only after completing these checks.

Retain the manifest, report, envelope, and lowercase hex signature together using
`package_evidence`. Store those bytes alongside the release metadata so admission
can run offline. The release validation digest continues to reference the
envelope. The gate rejects duplicate JSON keys in the archive and policy.

The approval flow records the binding returned by `verify_release_evidence`
for the reviewed release and exact target profile. The executor calls
`admit_release` with the approved binding before changing a workload. A raised
error blocks that proposed change. The local policy is read on each call so
issuer removal takes effect on the next admission. A changed policy or archive
requires a new approval binding.

## Optional serving-artifact security gate

Validation-only installations omit `security` from the installation policy and
publish validation-only releases. To require security evidence for every release
admitted under a policy, add a `security` object to the same operator-owned
policy file. All four checks in suite `triton-security-v1` are required together.

| Policy field | Check |
| --- | --- |
| `issuers` | Map of independently trusted security issuer names to hex Ed25519 public keys |
| `trivyVersion`, `banditVersion` | Exact scanner versions allowed to issue a passing result |
| `maxCodeFindings` | Maximum combined Bandit findings, detected secrets and failed configuration rules |
| `minPackages` | Minimum total inventoried packages, with a nonempty inventory required for each image |
| `maxImageFindings` | Maximum total HIGH and CRITICAL vulnerability findings in the runtime and configured materializer images |
| `approvedModelInventories` | Prepared inventory digests reviewed by the operator, covering file paths and bytes of every model, backend and config |
| `maxValiditySeconds` | Lifetime from the signed UTC scan timestamp |

Run the issuer in an isolated development/build environment with operator-installed
Trivy, Bandit (in the backend Python environment), Docker Buildx, registry read
access and current scanner databases. Use a trusted working directory, environment,
scanner configuration and PATH. Mount immutable source and prepared repositories
read-only. Supply a fresh private output directory path outside both inputs.
Keep the raw 32-byte Ed25519 signing key available only to the issuer.

```sh
TRITON_CONTROL_MODE=development python -m app.services.security_scan \
  --release release.json --source /inputs/source --models /inputs/models \
  --issuer build-security --key /keys/security-ed25519 --destination /reports/run-001
```

The issuer measures both repository inventories, scans serving files without
loading the model and inspects single-platform images by manifest digest. The
raw image reports contain package inventories and vulnerability findings. Keep
the raw reports private since code reports may identify secrets. On interruption,
rerun with a new output directory. Publication requires the final signed
`security.json`. Its SHA-256 is the release's `spec.security.digest`. Set
`spec.security.uri` to its retained location and copy its subjects into
`spec.security.subjects`. Pass the parsed signed report as the `security`
keyword to `package_evidence`. Production admission verifies the retained
signature, subjects, thresholds and time window offline. Existing validation-only
approvals preserve their binding when both optional security fields are absent.

An admitted result records the checks and limits in force at scan time.
Vulnerabilities outside the scanner's coverage and behavior of executable model
code still require independent review.
