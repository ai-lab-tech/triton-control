# Candidate lineage v1

S05-A introduces contracts under `contracts/lineage/v1`, generated with
`triton-backend/scripts/export_lineage_schema.py`. No HTTP endpoint is introduced.

Candidates bind an origin installation/record, source snapshot, ordered actual
engineering attempts and output inventory. Plugin references are optional.
Snapshot origins are preserved across installations. Candidate predecessors and
release replacements are different relationships. A runtime-only release reuses
the same candidate digest and selects a different pinned runtime image.

Canonical bytes: UTF-8 JSON, sorted object keys, compact separators, no ASCII
escaping, no floats/nonfinite numbers in contract fields. Defaults and null fields
are included. Inventory entries are sorted by UTF-8 path bytes. Paths must already
be NFC relative POSIX paths; traversal, empty components, duplicates and links are
rejected. File digest is SHA-256 of exact bytes; inventory digest hashes the full
versioned inventory. Candidate digest hashes the validated envelope. Neither
candidate digest nor publication receipts appear inside their own hashed payload.

Use a frozen model-only directory for measurement. Keep lineage documents,
receipts and reports outside that directory, in a separate metadata location.
The measurement helper detects per-file changes but is not a snapshot mechanism
for concurrently edited or hostile directories. Capture services must freeze inputs
before invoking it. It does not verify that caller-supplied provenance is truthful;
downstream authenticated capture/importers establish that evidence.

`publish` is initially admin-only and requires all referenced inventories plus an
existing predecessor when specified. It inserts an immutable envelope and inventories
under a composite origin key. Identical retries succeed; conflicting content fails.
The caller commits the transaction. Existing startup `create_tables` adds the
`serving_candidates` table without modifying legacy MLflow fields. No update/delete
publication API exists; database administrators remain outside this protection.

This initial v1 models a linear sequence of attempts; multi-input transformation
DAGs require a future contract version. ReleaseLineageV1 is a relationship contract,
not a production release approval or execution API.

Validation uses Arrange–Act–Assert unit tests for manual sources, candidate forks,
cross-installation origins, runtime-only releases, deterministic inventory order,
changed weights, unsafe paths/links, idempotency, conflicts and denied publication.
Real PostgreSQL 16 verification passed on 2026-11-15 in a disposable database:
identical concurrent publications produce one record, conflicting publications
preserve one winner, and rollback/retry preserves transaction semantics. All nine
lineage unit/integration tests passed. Run the opt-in integration suite with
`LINEAGE_TEST_DATABASE_URL` set to a disposable PostgreSQL database; each test uses
and removes its own random schema. No Kubernetes workload is needed for this contract.
