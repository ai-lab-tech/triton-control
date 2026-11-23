"""Exercise security admission, with an optional real registry and scanner run."""

import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.canonical import digest, inventory_digest
from app.schemas.gate_evidence import GatePolicy, SecurityPolicy, SecurityReport, SignedSecurityReport
from app.schemas.lineage import InventoryV1
from app.schemas.serving_metadata import ServingRelease
from app.schemas.validation import EvidenceEnvelope, ValidationManifest, ValidationReport
from app.services.candidates.artifacts import CaptureLimits
from app.services.contract_io import MetadataError
from app.services.gate_evidence import (
    POLICY_FILE,
    admit_release,
    package_evidence,
    security_message,
    signing_message,
    verify_release_evidence,
    verify_security,
)
from app.services.materialization.filesystem import inventory
from app.services.security_scan import main, scan
from tests.test_validation_contracts import NOW, data

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/validation/v1alpha1/fixtures/artifacts"


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.release = ServingRelease.model_validate(data("release"))
        subjects = self.release.spec.validation.subjects
        self.report = SecurityReport.model_validate(
            {
                "issuer": "scanner",
                "suite": "triton-security-v1",
                "subjects": subjects,
                "issuedAt": NOW,
                "trivyVersion": "0.76.2",
                "banditVersion": "1.9.4",
                "codeFindings": 0,
                "packages": 3,
                "imageFindings": 1,
                "codeReport": digest([]),
                "imageReport": digest([]),
                "modelInventory": InventoryV1.model_validate_json((FIXTURES / "inventory.json").read_bytes()),
            }
        )
        self.policy = SecurityPolicy.model_validate(
            {
                "issuers": {"scanner": self.key.public_key().public_bytes_raw().hex()},
                "trivyVersion": "0.76.2",
                "banditVersion": "1.9.4",
                "maxValiditySeconds": 60,
                "maxCodeFindings": 0,
                "minPackages": 3,
                "maxImageFindings": 1,
                "approvedModelInventories": [subjects.prepared_inventory_digest],
            }
        )
        self.signed = SignedSecurityReport(
            report=self.report, signature=self.key.sign(security_message(self.report)).hex()
        )
        self.release = self.reference(self.release, self.signed)

    def reference(self, release, signed):
        doc = release.model_dump(mode="json", by_alias=True)
        doc["spec"]["security"] = {
            "uri": "security.json",
            "subjects": doc["spec"]["validation"]["subjects"],
            "digest": digest(signed.model_dump(mode="json", by_alias=True)),
        }
        return ServingRelease.model_validate(doc)

    def test_thresholds_validity_and_authenticated_report(self):
        verify_security(self.release, self.signed, self.policy, NOW)
        for change in [
            {"min_packages": 4},
            {"max_image_findings": 0},
            {"trivy_version": "0.69.2"},
            {"approved_model_inventories": ()},
            {"issuers": {}},
        ]:
            with self.subTest(change=change), self.assertRaises(MetadataError):
                verify_security(self.release, self.signed, self.policy.model_copy(update=change), NOW)
        for signed in [None, self.signed.model_copy(update={"signature": "00" * 64})]:
            with self.assertRaises(MetadataError):
                verify_security(self.release, signed, self.policy, NOW)
        with self.assertRaises(MetadataError):
            verify_security(self.release, self.signed, self.policy, NOW + timedelta(seconds=60))

    def test_offline_admission_requires_security_when_enabled(self):
        validator = Ed25519PrivateKey.generate()
        manifest = ValidationManifest.model_validate(data("manifest"))
        result = ValidationReport.model_validate(data("report-passed"))
        envelope = EvidenceEnvelope.model_validate(data("evidence"))
        signature = validator.sign(signing_message(envelope)).hex()
        archive = package_evidence(manifest, result, envelope, signature, security=self.signed)
        policy = GatePolicy.model_validate(
            {
                "identity": envelope.policy,
                "issuers": {envelope.issuer: validator.public_key().public_bytes_raw().hex()},
                "evaluator": manifest.evaluator,
                "requiredCriteria": manifest.criteria,
                "maxValiditySeconds": int((envelope.valid_until - envelope.issued_at).total_seconds()),
                "security": {
                    **self.policy.model_dump(by_alias=True),
                    "approvedModelInventories": [
                        *self.policy.approved_model_inventories,
                        *(digest(i) for i in range(9999)),
                    ],
                },
            }
        )
        target = manifest.supported_targets[0]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.json"
            path.write_text(policy.model_dump_json(by_alias=True, indent=2))
            with patch.dict(os.environ, {POLICY_FILE: str(path)}):
                binding = verify_release_evidence(self.release, archive, now=NOW, target=target)
                self.assertEqual(
                    binding, admit_release(self.release, archive, now=NOW, target=target, approved_binding=binding)
                )
                with self.assertRaisesRegex(MetadataError, "security-policy-report-or-reference-missing"):
                    verify_release_evidence(
                        ServingRelease.model_validate(data("release")),
                        package_evidence(manifest, result, envelope, signature),
                        now=NOW,
                        target=target,
                    )

    def test_changed_release_artifacts(self):
        for kind in ["image", "inventoryDigest"]:
            with self.subTest(kind=kind):
                doc = self.release.model_dump(mode="json", by_alias=True)
                spec = doc["spec"]
                for field in ["security", "validation"]:
                    spec[field]["subjects"][kind] = digest("changed")
                if kind == "image":
                    spec["image"] = spec["lineage"]["runtime_image"] = "registry.example/test@" + digest("changed")
                else:
                    spec["repository"][kind] = spec["repository"]["preparedInventoryDigest"] = digest("changed")
                    for field in ["security", "validation"]:
                        spec[field]["subjects"]["preparedInventoryDigest"] = digest("changed")
                with self.assertRaisesRegex(MetadataError, "security-subject-mismatch"):
                    verify_security(ServingRelease.model_validate(doc), self.signed, self.policy, NOW)

    def test_scanner_refuses_production(self):
        with (
            patch.dict(os.environ, TRITON_CONTROL_MODE="production"),
            self.assertRaisesRegex(ValueError, "development"),
        ):
            scan(self.release, Path("."), Path("."), Path("."), "scanner", Path("."))

    def test_cli_applies_materialization_budget_to_both_inventories(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, TRITON_CONTROL_MODE="development"):
            root = Path(temp)
            release = root / "release.json"
            release.write_text(json.dumps(data("release")))
            budget = root / "limits.json"
            budget.write_text(json.dumps({"max_bytes": 40 << 30, "max_file_bytes": 40 << 30, "deadline_seconds": 3600}))
            args = [
                "scan",
                "--release",
                str(release),
                "--source",
                str(root / "source"),
                "--models",
                str(root / "models"),
                "--destination",
                str(root / "out"),
                "--issuer",
                "scanner",
                "--key",
                str(root / "key"),
                "--limits",
                str(budget),
            ]
            expected = CaptureLimits(max_bytes=40 << 30, max_file_bytes=40 << 30, deadline_seconds=3600)
            with (
                patch("sys.argv", args),
                patch("app.services.security_scan.inventory", return_value=self.report.model_inventory) as measure,
                patch("app.services.security_scan.run_trivy", side_effect=ValueError("end of inventory")),
                self.assertRaisesRegex(ValueError, "end of inventory"),
            ):
                main()
            self.assertEqual(
                [c.args for c in measure.call_args_list], [(root / "models", expected), (root / "source", expected)]
            )

    def test_failure_leaves_no_signed_receipt_and_changed_inputs_block_retry(self):
        release = ServingRelease.model_validate(data("release"))
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, TRITON_CONTROL_MODE="development"):
            root = Path(temp)
            models = root / "models"
            (models / "toy/1").mkdir(parents=True)
            weights = models / "toy/1/weights.txt"
            shutil.copyfile(FIXTURES / "weights.txt", weights)
            with patch("app.services.security_scan.run_trivy", side_effect=ValueError("scanner unavailable")):
                with self.assertRaisesRegex(ValueError, "scanner unavailable"):
                    scan(release, models, models, root / "first", "scanner", root / "key")
            self.assertFalse((root / "first/security.json").exists())
            weights.write_bytes(b"updated")
            with self.assertRaisesRegex(ValueError, "inventories differ"):
                scan(release, models, models, root / "retry", "scanner", root / "key")
            self.assertFalse((root / "retry/security.json").exists())

    @unittest.skipUnless(
        os.getenv("SECURITY_TEST_IMAGE"), "requires Trivy and a registry image pinned by manifest digest"
    )
    def test_registry_and_model_scan(self):
        doc = data("release")
        image = os.environ["SECURITY_TEST_IMAGE"]
        doc["spec"]["image"] = doc["spec"]["lineage"]["runtime_image"] = image
        doc["spec"]["validation"]["subjects"]["image"] = image.split("@")[1]
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, TRITON_CONTROL_MODE="development"):
            root = Path(temp)
            models = root / "models"
            (models / "toy/1").mkdir(parents=True)
            shutil.copyfile(FIXTURES / "weights.txt", models / "toy/1/weights.txt")
            (models / "toy/1/model.py").write_text("assert True\n")
            measured = inventory_digest(inventory(models, CaptureLimits()))
            for field in ["inventoryDigest", "preparedInventoryDigest"]:
                doc["spec"]["repository"][field] = doc["spec"]["validation"]["subjects"][field] = measured
            release = ServingRelease.model_validate(doc)
            key = root / "key"
            key.write_bytes(self.key.private_bytes_raw())
            scan(release, models, models, root / "output", "scanner", key)
            signed = SignedSecurityReport.model_validate_json((root / "output/security.json").read_bytes())
            raw = json.loads((root / "output/image-0.json").read_bytes())
            self.assertEqual(signed.report.image_report, digest([raw]))
            self.assertGreater(signed.report.packages, 0)
            self.assertGreater(signed.report.code_findings, 0)
            policy = self.policy.model_copy(
                update={
                    "max_image_findings": signed.report.image_findings,
                    "min_packages": 1,
                    "approved_model_inventories": (measured,),
                    "max_code_findings": signed.report.code_findings,
                }
            )
            verify_security(self.reference(release, signed), signed, policy, signed.report.issued_at)
