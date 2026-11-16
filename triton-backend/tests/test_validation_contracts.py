"""S07-A contract compatibility, stale evidence and fail-closed fixture tests."""

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from pydantic import ValidationError

from app.schemas.validation import (
    EvidenceEnvelope,
    HardwareRuntime,
    PolicyIdentity,
    ValidationManifest,
    ValidationReport,
)
from app.services.serving_metadata import MetadataError, load_metadata
from app.services.validation import (
    artifact_subjects,
    check_evidence_consistency,
    check_release_binding,
    check_report,
    document_digest,
    load_validation,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "contracts/validation/v1alpha1/fixtures"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
OTHER_DIGEST = "sha256:" + hashlib.sha256(b"synthetic modified content").hexdigest()
OTHER_IMAGE = "example.invalid/modified@" + OTHER_DIGEST


def data(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / (name + ".json")).read_bytes())


class ValidationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = ValidationManifest.model_validate(data("manifest"))
        self.report = ValidationReport.model_validate(data("report-passed"))
        self.envelope = EvidenceEnvelope.model_validate(data("evidence"))

    def check(self, manifest=None, report=None, envelope=None, target=None, policy=None, now=NOW):
        check_evidence_consistency(
            manifest or self.manifest, report or self.report, envelope or self.envelope,
            target=target or self.manifest.supported_targets[0], policy=policy or self.envelope.policy, now=now,
        )

    def test_golden_yaml_json_canonical_and_hashes(self):
        hashes = data("hashes")
        for name in ("manifest", "report-passed", "report-failed", "report-error", "report-unsupported", "evidence"):
            with self.subTest(name=name):
                parsed = [load_validation((FIXTURES / (name + ext)).read_bytes()) for ext in (".json", ".yaml")]
                self.assertEqual(parsed[0], parsed[1])
                self.assertEqual(parsed[0].digest, hashes[name])
                self.assertEqual(parsed[0].canonical, (FIXTURES / "canonical" / (name + ".json")).read_bytes())
        self.check()

    def test_real_fixture_bytes_match_referenced_hashes(self):
        for ref in (self.manifest.dataset, self.manifest.expected_outputs, self.manifest.evaluator.code,
                    self.manifest.startup.configuration):
            self.assertEqual(ref.digest, "sha256:" + hashlib.sha256((FIXTURES / ref.uri).read_bytes()).hexdigest())
        self.assertEqual(self.envelope.report.digest, document_digest(self.report))
        self.assertEqual(self.report.subjects.validation_input, document_digest(self.manifest))

    def test_each_manifest_input_invalidates_previous_evidence(self):
        mutations = [
            (("image",), OTHER_IMAGE),
            (("repository", "uri"), "s3://synthetic-models/changed/"),
            (("repository", "inventoryDigest"), OTHER_DIGEST),
            (("repository", "preparedInventoryDigest"), OTHER_DIGEST),
            (("repository", "materializerImage"), OTHER_IMAGE),
            (("sourceCode", "digest"), OTHER_DIGEST),
            (("dataset", "digest"), OTHER_DIGEST),
            (("expectedOutputs", "digest"), OTHER_DIGEST),
            (("evaluator", "image"), OTHER_IMAGE),
            (("evaluator", "version"), "2"),
            (("evaluator", "code", "digest"), OTHER_DIGEST),
            (("criteria", 0, "threshold"), "0.96"),
            (("criteria", 0, "operator"), "lte"),
            (("startup", "configuration", "digest"), OTHER_DIGEST),
            (("startup", "models", 0, "versions"), ["2"]),
            (("startup", "resourcesPerReplica", "requests", "cpu"), "500m"),
            (("supportedTargets", 0, "architecture"), "arm64"),
            (("supportedTargets", 0, "cpuModel"), "different-cpu"),
            (("supportedTargets", 0, "tritonVersion"), "different-runtime"),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                revised = data("manifest")
                node = revised
                for part in path[:-1]:
                    node = node[part]
                node[path[-1]] = value
                if path in (("repository", "inventoryDigest"), ("repository", "preparedInventoryDigest")):
                    revised["repository"]["materializerImage"] = OTHER_IMAGE
                manifest = ValidationManifest.model_validate(revised)
                self.assertNotEqual(document_digest(manifest), document_digest(self.manifest))
                with self.assertRaisesRegex(MetadataError, "stale-or-mismatched-subjects"):
                    self.check(manifest=manifest)
        self.assertEqual(document_digest(self.report), self.envelope.report.digest)

    def test_scale_only_preserves_artifact_validation_not_fleet_performance(self):
        target, scaled = [
            load_metadata((FIXTURES / name).read_bytes()) for name in ("target.json", "target-scaled.json")
        ]
        self.assertNotEqual(target.digest, scaled.digest)
        self.assertEqual(target.document.spec.release, scaled.document.spec.release)
        self.check()
        self.assertEqual(self.report.performance_scope, "single-replica")
        for name in ("manifest", "report-passed"):
            modified = data(name)
            modified["performanceScope"] = "fleet"
            with self.assertRaises(MetadataError):
                load_validation(json.dumps(modified).encode())

    def test_unsupported_hardware_remains_explicit_and_cannot_pass(self):
        report = ValidationReport.model_validate(data("report-unsupported"))
        check_report(self.manifest, report)
        with self.assertRaisesRegex(MetadataError, "validation-not-passed"):
            self.check(report=report)
        with self.assertRaisesRegex(MetadataError, "unsupported-target"):
            self.check(target=report.measured_target)
        forged = data("report-passed")
        forged["measuredTarget"]["architecture"] = "arm64"
        with self.assertRaisesRegex(MetadataError, "unsupported-measured-target"):
            self.check(report=ValidationReport.model_validate(forged))

    def test_gpu_profile_is_explicit_and_resources_match(self):
        revised = data("manifest")
        profile = revised["supportedTargets"][0]
        profile.update(gpuCount=1, gpuModel="synthetic-gpu", driverVersion="1", cudaVersion="1")
        with self.assertRaises(ValidationError):
            ValidationManifest.model_validate(revised)
        revised["startup"]["resourcesPerReplica"]["gpuCount"] = 1
        manifest = ValidationManifest.model_validate(revised)
        self.assertNotEqual(document_digest(manifest), document_digest(self.manifest))
        for field in ("gpuModel", "driverVersion", "cudaVersion"):
            wrong = {**profile, field: None}
            with self.assertRaises(ValidationError):
                HardwareRuntime.model_validate(wrong)

    def test_pass_flags_are_derived_from_measured_values(self):
        revised = data("report-passed")
        revised["metrics"][0]["value"] = "0.94"
        with self.assertRaises(ValidationError):
            ValidationReport.model_validate(revised)
        failed = ValidationReport.model_validate(data("report-failed"))
        check_report(self.manifest, failed)
        with self.assertRaisesRegex(MetadataError, "validation-not-passed"):
            self.check(report=failed)

    def test_missing_extra_or_changed_metrics_cannot_claim_pass(self):
        for change in ("missing", "extra", "threshold", "evaluator", "subjects"):
            revised = data("report-passed")
            if change == "missing":
                revised["metrics"].pop()
            elif change == "extra":
                extra = copy.deepcopy(revised["metrics"][0])
                extra["criterion"]["metric"] = "unexpected"
                revised["metrics"].append(extra)
            elif change == "threshold":
                revised["metrics"][0]["criterion"]["threshold"] = "0.90"
            elif change == "evaluator":
                revised["evaluator"]["version"] = "2"
            else:
                revised["subjects"]["image"] = OTHER_DIGEST
            with self.subTest(change=change), self.assertRaises(MetadataError):
                self.check(report=ValidationReport.model_validate(revised))

    def test_empty_duplicate_and_nonfinite_metrics_rejected(self):
        for value in (None, float("nan"), 0.97, True, "NaN", "Infinity", "1e99"):
            revised = data("report-passed")
            revised["metrics"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ValidationReport.model_validate(revised)
        for metrics in ([], [data("report-passed")["metrics"][0]] * 2):
            revised = data("report-passed") | {"metrics": metrics}
            with self.assertRaises(ValidationError):
                ValidationReport.model_validate(revised)

    def test_runner_failure_before_measurement_does_not_invent_hardware(self):
        report = ValidationReport.model_validate(data("report-error"))
        self.assertIsNone(report.measured_target)
        check_report(self.manifest, report)
        with self.assertRaisesRegex(MetadataError, "validation-not-passed"):
            self.check(report=report)
        with self.assertRaises(ValidationError):
            ValidationReport.model_validate(data("report-passed") | {"measuredTarget": None})

    def test_error_timeout_and_missing_metrics_fail_closed(self):
        for code in ("timeout", "runner-failure", "missing-metric", "artifact-mismatch", "cancelled"):
            report = ValidationReport.model_validate(data("report-passed") | {
                "status": "error", "metrics": [], "errors": [{"code": code}],
            })
            check_report(self.manifest, report)
            with self.assertRaisesRegex(MetadataError, "validation-not-passed"):
                self.check(report=report)
        with self.assertRaises(ValidationError):
            ValidationReport.model_validate(data("report-passed") | {"status": "error"})

    def test_policy_validity_tool_and_report_mismatches(self):
        mutations = [
            (("report", "digest"), OTHER_DIGEST),
            (("subjects", "image"), OTHER_DIGEST),
            (("tools", 0, "version"), "2"),
            (("issuedAt",), "2026-01-01T00:00:30Z"),
        ]
        for path, value in mutations:
            revised = data("evidence")
            node = revised
            for part in path[:-1]:
                node = node[part]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(MetadataError):
                self.check(envelope=EvidenceEnvelope.model_validate(revised))
        with self.assertRaisesRegex(MetadataError, "policy-mismatch"):
            self.check(policy=PolicyIdentity(name="synthetic-policy", version="2"))
        for now in (datetime(2025, 1, 1, tzinfo=timezone.utc), self.envelope.valid_until):
            with self.assertRaisesRegex(MetadataError, "evidence-outside-validity"):
                self.check(now=now)
        with self.assertRaisesRegex(MetadataError, "timezone-required"):
            self.check(now=NOW.replace(tzinfo=None))

    def test_timestamps_require_timezones_and_normalize_offsets(self):
        revised = data("report-passed")
        revised["startedAt"] = "2026-01-01T01:00:00+01:00"
        report = ValidationReport.model_validate(revised)
        self.assertEqual(document_digest(report), document_digest(self.report))
        for start in ("2026-01-01T00:00:00", "2026-01-02T00:00:00Z"):
            with self.assertRaises(ValidationError):
                ValidationReport.model_validate(revised | {"startedAt": start})
        with self.assertRaises(ValidationError):
            EvidenceEnvelope.model_validate(data("evidence") | {"validUntil": "2026-01-01T00:02:00Z"})

    def test_release_evidence_binding_is_acyclic(self):
        release = load_metadata((FIXTURES / "release.json").read_bytes()).document
        check_release_binding(release, self.manifest, self.envelope)
        self.assertNotIn("release", data("manifest"))
        self.assertNotIn("release", data("evidence"))
        for field in ("image", "resources", "models", "evidence"):
            revised = data("release")
            if field == "image":
                revised["spec"]["image"] = OTHER_IMAGE
                revised["spec"]["lineage"]["runtime_image"] = OTHER_IMAGE
                revised["spec"]["validation"]["subjects"]["image"] = OTHER_DIGEST
            elif field == "resources":
                revised["spec"]["resourcesPerReplica"]["requests"]["cpu"] = "500m"
            elif field == "models":
                revised["spec"]["models"][0]["versions"] = ["2"]
            else:
                revised["spec"]["validation"]["digest"] = OTHER_DIGEST
            changed = load_metadata(json.dumps(revised).encode()).document
            with self.subTest(field=field), self.assertRaisesRegex(MetadataError, "release-validation-mismatch"):
                check_release_binding(changed, self.manifest, self.envelope)

    def test_artifact_subjects_match_s08_shape(self):
        self.assertEqual(artifact_subjects(self.manifest), self.report.subjects)
        revised = data("manifest")
        revised["repository"]["materializerImage"] = OTHER_IMAGE
        revised["repository"]["preparedInventoryDigest"] = OTHER_DIGEST
        subjects = artifact_subjects(ValidationManifest.model_validate(revised))
        self.assertEqual(subjects.materializer_image, OTHER_DIGEST)

    def test_unknown_versions_fields_manifests_and_cycles_rejected(self):
        for patch_data in ({"apiVersion": "validation.triton-control/v2"}, {"kind": "Deployment"},
                           {"releaseDigest": OTHER_DIGEST}, {"credentials": "SECRET"}):
            with self.assertRaises(MetadataError):
                load_validation(json.dumps(data("manifest") | patch_data).encode())
        with self.assertRaises(MetadataError):
            load_validation(b"[]")
        with self.assertRaises(MetadataError):
            load_validation(b"{}", format="xml")

    def test_untrusted_parser_limits_and_errors_do_not_leak_values(self):
        for raw in (b"x: !!python/object:SECRET {}", b"x: &SECRET [*SECRET]", b"x: 1\nx: 2", b"---\nx: 1\n---\ny: 2",
                    b"[" * 40 + b"0" + b"]" * 40, b" " * (1024 * 1024 + 1), b"{SECRET: [}"):
            with self.assertRaises(MetadataError) as error:
                load_validation(raw)
            self.assertNotIn("SECRET", str(error.exception))
        invalid = data("manifest")
        invalid["dataset"]["uri"] = "https://user:SECRET@example.invalid/data"
        with self.assertRaises(MetadataError) as error:
            load_validation(json.dumps(invalid).encode())
        self.assertNotIn("SECRET", str(error.exception))

    def test_contract_loading_never_executes_or_fetches_evaluator(self):
        with patch("socket.socket", side_effect=AssertionError("must be offline")):
            load_validation((FIXTURES / "manifest.json").read_bytes())
            self.check()
        # Even an untrusted fixture issuer passes STRUCTURAL consistency only;
        # authentication belongs to S07-C and is not fabricated by this module.
        self.assertEqual(self.envelope.issuer, "synthetic-untrusted-issuer")

    def test_cli_and_export_drift_checks(self):
        for script in ("export_validation_schema.py", "generate_validation_fixtures.py"):
            result = subprocess.run([sys.executable, str(ROOT / "triton-backend/scripts" / script), "--check"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([sys.executable, "-m", "app.services.validation_cli", str(FIXTURES / "manifest.json")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["validation"], "structure-and-semantics-only")
        result = subprocess.run([sys.executable, "-m", "app.services.validation_cli", "-"],
                                input=b"secret: SECRET", capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b"SECRET", result.stderr)
