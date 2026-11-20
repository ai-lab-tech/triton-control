# MLflow Control lineage and explicit handoffs (S05-D)

The optional `mlflow-triton-control` adapter explicitly submits prepared requests
to `POST /api/preparation/handoff-captures`. Plain MLflow logging remains unchanged.
This endpoint requires an interactive, active member/admin and an idempotency key;
existing development-mode and source-profile authorization still applies.

## Contract and identity

`HandoffRequest.lineage` and `CreateDeploymentRequest.lineage` optionally carry the
version-one `ControlLineage` contract in
`contracts/lineage/v1/control-lineage.schema.json`. Its `origin` is the exact logged
model ID plus tracking installation, never an S3 address. Derived records retain
the candidate, training-source reference, predecessor, conversion run and original
lineage manifest digest. Legacy origins require an explicitly selected installation
and do not invent derived provenance. The contract rejects extra approval fields
and inconsistent source/predecessor references.

MLflow handoffs require repository-folder inputs. Capture verifies all exact S3
versions and checks the measured repository inventory against the declared
candidate output. The handoff snapshot retains the MLflow origin. Receipts use
that origin and revision for deduplication, independently of storage location;
retries recheck current profile authorization and return the first immutable
receipt. Changed lineage or requirements require a new revision. Concurrent
publication is serialized by the existing installation transaction lock. Plain
S3 handoffs retain their original identities and semantics.

The adapter resolves aliases when preparing the JSON request. Clients persist that
request and its key before sending and reuse both after an uncertain result.
Preparing again is a new resolution, not a retry. A cancelled submission needs a
new key to start a new job. The public operation APIs provide progress and recovery.

## Deployment and export

Development deployment admission stores typed lineage in the existing durable
intent response, in the same transaction as the deployment record. Owner-scoped
MLflow deployment reads and lists return it under `lineage`. The optional adapter
exports that value without deriving origin from the current artifact address.
Legacy records return null. The existing `model_uri` storage field is retained for
old clients. Keyless legacy deployment fingerprints omit the absent new field so
upgrade does not change their retry identity.

These are producer-supplied provenance references, not production approval or a
validation result. No release is approved or production mutation enabled. Core
MLflow logging has no dependency on Control APIs.

## Upgrade and verification

The changes add optional request/response fields and JSON payload content only;
no tables, columns, existing receipts or deployment records need rewriting.
Upgrade Control before using the new optional plugin methods. Regenerate OpenAPI
and keep both copies in sync. Export the portable schema with
`python scripts/export_lineage_schema.py`; its copy is packaged in the plugin.

Unit/contract tests cover compatibility, authorization, identity across relocation,
candidate inventory mismatch and persisted export. `test_control_lineage_postgres`
adds real PostgreSQL concurrency coverage to the existing `teams-postgres` CI job.
The existing deployment PostgreSQL/Kubernetes suites exercise admission and recovery;
the persisted-lineage test also runs through the shared PostgreSQL fixture.
Long-running provider checks remain GitHub CI responsibilities.
