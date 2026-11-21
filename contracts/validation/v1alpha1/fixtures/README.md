# Synthetic validation fixtures

All artifacts, measurements, timestamps, issuer/policy identities and image
descriptors here are **synthetic and non-deployable**. No real registry image,
benchmark, signature or approval is claimed. Artifact SHA-256 values are computed
from the committed bytes (including copied synthetic serving metadata descriptors).

`manifest`, `report-passed`, `report-failed`, `report-unsupported` and `evidence`
exercise the validation contracts. `release`, `target` and `target-scaled` demonstrate
the serving metadata
boundary. Both targets select exactly the same release, while their target hashes
differ. These fixtures make no fleet throughput or latency claim.

JSON/YAML pairs normalize to the bytes in `canonical/`; `hashes.json` pins their
canonical document identities. References to reports/evidence hash normalized
contract bytes, not the pretty-printed transport file. Artifact references hash
raw bytes. Regenerate with `scripts/generate_validation_fixtures.py`; CI uses
`--check` to detect drift. These examples must never be used as real gate evidence.
