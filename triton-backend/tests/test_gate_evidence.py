import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.schemas.gate_evidence import EvidenceArchive, GatePolicy
from app.schemas.serving_metadata import ServingRelease
from app.schemas.validation import EvidenceEnvelope, ValidationManifest, ValidationReport
from app.services.contract_io import MetadataError
from app.services.gate_evidence import POLICY_FILE, admit_release, signing_message, verify_release_evidence
from tests.test_validation_contracts import NOW, data


class GateEvidenceTests(unittest.TestCase):
    def test_rechecks_signed_evidence_and_local_trust(self) -> None:
        key = Ed25519PrivateKey.generate()
        manifest = ValidationManifest.model_validate(data("manifest"))
        envelope = EvidenceEnvelope.model_validate(data("evidence"))
        archive = EvidenceArchive(
            manifest=manifest, envelope=envelope, report=ValidationReport.model_validate(data("report-passed")),
            signature=key.sign(signing_message(envelope)).hex(),
        )
        release = ServingRelease.model_validate(data("release"))
        target = manifest.supported_targets[0]
        policy = GatePolicy.model_validate({
            "identity": envelope.policy,
            "issuers": {envelope.issuer: key.public_key().public_bytes_raw().hex()},
            "evaluator": manifest.evaluator,
            "requiredCriteria": manifest.criteria,
            "maxValiditySeconds": int((envelope.valid_until - envelope.issued_at).total_seconds()),
        })
        raw = archive.model_dump_json(by_alias=True).encode()
        with TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            path.write_text(policy.model_dump_json(by_alias=True))
            with patch.dict(os.environ, {POLICY_FILE: str(path)}):
                binding = verify_release_evidence(release, raw, target=target, now=NOW)
                with self.assertRaisesRegex(MetadataError, "evidence-outside-validity"):
                    verify_release_evidence(release, raw, target=target, now=envelope.valid_until)
                tampered = archive.model_copy(update={"signature": "00" * 64})
                with self.assertRaisesRegex(MetadataError, "invalid-evidence-signature"):
                    verify_release_evidence(release, tampered.model_dump_json(by_alias=True).encode(), target=target, now=NOW)
                changed = policy.model_copy(update={"max_validity_seconds": policy.max_validity_seconds + 1})
                path.write_text(changed.model_dump_json(by_alias=True))
                with self.assertRaisesRegex(MetadataError, "gate-evidence-approval-invalidated"):
                    admit_release(release, raw, target=target, now=NOW, approved_binding=binding)
                path.write_text(policy.model_copy(update={"issuers": {
                    envelope.issuer: Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex(),
                }}).model_dump_json(by_alias=True))
                with self.assertRaisesRegex(MetadataError, "invalid-evidence-signature"):
                    admit_release(release, raw, target=target, now=NOW, approved_binding=binding)
