"""Offline gate admission, trust policy, and archive parsing."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.schemas.gate_evidence import GatePolicy
from app.schemas.serving_metadata import ServingRelease
from app.schemas.validation import EvidenceEnvelope, ValidationManifest, ValidationReport
from app.services.contract_io import MetadataError
from app.services.gate_evidence import (
    POLICY_FILE,
    admit_release,
    package_evidence,
    signing_message,
    verify_release_evidence,
)
from tests.test_validation_contracts import NOW, data


class GateEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        key = Ed25519PrivateKey.generate()
        self.manifest = ValidationManifest.model_validate(data("manifest"))
        self.report = ValidationReport.model_validate(data("report-passed"))
        self.envelope = EvidenceEnvelope.model_validate(data("evidence"))
        self.release = ServingRelease.model_validate(data("release"))
        self.target = self.manifest.supported_targets[0]
        self.signature = key.sign(signing_message(self.envelope)).hex()
        self.policy = GatePolicy.model_validate({
            "identity": self.envelope.policy,
            "issuers": {self.envelope.issuer: key.public_key().public_bytes_raw().hex()},
            "evaluator": self.manifest.evaluator,
            "requiredCriteria": self.manifest.criteria,
            "maxValiditySeconds": int((self.envelope.valid_until - self.envelope.issued_at).total_seconds()),
        })
        directory = self.enterContext(TemporaryDirectory())
        self.policy_path = Path(directory) / "policy.json"
        self.policy_path.write_text(self.policy.model_dump_json(by_alias=True), encoding="utf-8")
        self.enterContext(patch.dict(os.environ, {POLICY_FILE: str(self.policy_path)}))

    def package(self, signature: str) -> bytes:
        return package_evidence(self.manifest, self.report, self.envelope, signature)

    def approve(self, raw: bytes) -> str:
        return verify_release_evidence(self.release, raw, target=self.target, now=NOW)

    def test_admits_packaged_evidence_with_matching_approval(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)

        # Act
        binding = admit_release(self.release, raw, target=self.target, now=NOW, approved_binding=approved)

        # Assert
        self.assertRegex(binding, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(binding, approved)

    def test_relocated_inventory_requires_its_own_approval(self) -> None:
        raw = self.package(self.signature)
        original = self.approve(raw)
        payload = self.release.model_dump(mode="json", by_alias=True)
        repository = payload["spec"]["repository"]
        repository["uri"] = "s3://production/promoted/models/" + repository["inventoryDigest"][7:] + "/"
        promoted = ServingRelease.model_validate(payload)
        binding = verify_release_evidence(promoted, raw, target=self.target, now=NOW)
        self.assertNotEqual(original, binding)
        with self.assertRaisesRegex(MetadataError, "gate-evidence-approval-invalidated"):
            admit_release(promoted, raw, target=self.target, now=NOW, approved_binding=original)

    def test_admission_rejects_expired_evidence(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "evidence-outside-validity"):
            admit_release(
                self.release, raw, target=self.target, now=self.envelope.valid_until, approved_binding=approved,
            )

    def test_admission_rejects_tampered_signature(self) -> None:
        # Arrange
        approved = self.approve(self.package(self.signature))
        tampered = self.package("00" * 64)

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "invalid-evidence-signature"):
            admit_release(self.release, tampered, target=self.target, now=NOW, approved_binding=approved)

    def test_admission_rejects_removed_issuer(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)
        public_key = self.policy.issuers[self.envelope.issuer]
        changed = self.policy.model_copy(update={"issuers": {"replacement-issuer": public_key}})
        self.policy_path.write_text(changed.model_dump_json(by_alias=True), encoding="utf-8")

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "untrusted-evidence-issuer"):
            admit_release(self.release, raw, target=self.target, now=NOW, approved_binding=approved)

    def test_admission_rechecks_rotated_issuer_key(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)
        replacement_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw().hex()
        changed = self.policy.model_copy(update={"issuers": {self.envelope.issuer: replacement_key}})
        self.policy_path.write_text(changed.model_dump_json(by_alias=True), encoding="utf-8")

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "invalid-evidence-signature"):
            admit_release(self.release, raw, target=self.target, now=NOW, approved_binding=approved)

    def test_admission_invalidates_approval_after_policy_change(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)
        changed = self.policy.model_copy(update={"max_validity_seconds": self.policy.max_validity_seconds + 1})
        self.policy_path.write_text(changed.model_dump_json(by_alias=True), encoding="utf-8")

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "gate-evidence-approval-invalidated"):
            admit_release(self.release, raw, target=self.target, now=NOW, approved_binding=approved)

    def test_admission_rejects_duplicate_archive_keys(self) -> None:
        raw = self.package(self.signature)
        approved = self.approve(raw)
        for field in (b'"signature":', b'"issuer":'):
            with self.subTest(field=field):
                # Arrange
                ambiguous = raw.replace(field, field + b'"invalid",' + field, 1)
                self.assertNotEqual(ambiguous, raw)

                # Act / Assert
                with self.assertRaisesRegex(MetadataError, "invalid-evidence-archive"):
                    admit_release(self.release, ambiguous, target=self.target, now=NOW, approved_binding=approved)

    def test_admission_rejects_duplicate_policy_keys(self) -> None:
        # Arrange
        raw = self.package(self.signature)
        approved = self.approve(raw)
        policy = self.policy_path.read_bytes()
        field = b'"maxValiditySeconds":'
        ambiguous = policy.replace(field, field + b"0," + field, 1)
        self.assertNotEqual(ambiguous, policy)
        self.policy_path.write_bytes(ambiguous)

        # Act / Assert
        with self.assertRaisesRegex(MetadataError, "invalid-gate-policy"):
            admit_release(self.release, raw, target=self.target, now=NOW, approved_binding=approved)
