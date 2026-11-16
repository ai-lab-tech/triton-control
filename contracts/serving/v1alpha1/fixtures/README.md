# Synthetic conformance fixtures — not deployable

These documents are golden test vectors, not real releases, credentials, approved
reports or registry images. Every digest is derived from the committed synthetic
artifact bytes or canonical metadata. Image descriptor text is deliberately not
an OCI image. `.invalid` hosts cannot represent real production endpoints.

`hashes.json` records expected identities; `canonical/` records exact normalized
bytes. Paired JSON/YAML documents must produce identical bytes and digests.
`artifacts/` supplies deterministic provenance/inventory/evidence test inputs.

Reproduce with `python scripts/generate_serving_metadata_fixtures.py` from
`triton-backend`; `--check` fails on drift without changing files. Generation is an
explicit maintainer action, never part of accepting metadata. See
[the contract documentation](../../../../docs/serving-metadata-contracts.md).
