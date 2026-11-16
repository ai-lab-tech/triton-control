"""Conformance, adversarial-input and identity tests for the offline contract."""

import copy
import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from jsonschema import Draft202012Validator

from app.schemas.lineage import InventoryV1, ServingCandidateV1
from app.services.lineage import digest, inventory_digest
from app.services.serving_metadata import MAX_BYTES, MetadataError, load_metadata

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "contracts/serving/v1alpha1"
FIXTURES = SCHEMAS / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class ServingMetadataTests(unittest.TestCase):
    def reject(self, document: Any) -> None:
        with self.assertRaises(MetadataError):
            load_metadata(json.dumps(document).encode())

    def test_golden_formats_and_hashes(self) -> None:
        vectors = fixture("hashes")["documents"]
        for name, expected in vectors.items():
            for extension in ("json", "yaml"):
                with self.subTest(name=name, format=extension):
                    result = load_metadata((FIXTURES / f"{name}.{extension}").read_bytes())
                    self.assertEqual(result.digest, expected)
                    self.assertEqual(result.canonical, (FIXTURES / "canonical" / f"{name}.json").read_bytes())
                    self.assertEqual(result.digest, "sha256:" + hashlib.sha256(result.canonical).hexdigest())

    def test_shared_schemas_and_packaged_copies(self) -> None:
        for path in SCHEMAS.glob("*.schema.json"):
            schema = json.loads(path.read_text())
            Draft202012Validator.check_schema(schema)
            packaged = ROOT / "triton-backend/app/services/serving_contracts" / path.name
            self.assertEqual(path.read_bytes(), packaged.read_bytes())
            validator = Draft202012Validator(schema)
            for name in fixture("hashes")["documents"]:
                data = fixture(name)
                if data["kind"] == path.name.split(".")[0]:
                    self.assertTrue(validator.is_valid(data))
                    data["spec"]["arbitraryKubernetesManifest"] = {"kind": "Pod"}
                    self.assertFalse(validator.is_valid(data))

    def test_source_defaults_are_explicit_in_identity(self) -> None:
        data = fixture("source-git")
        implicit = load_metadata(json.dumps(data).encode())
        data["spec"].update({"intervalSeconds": 86400, "approval": {"mode": "manual"}, "enabled": False})
        self.assertEqual(implicit.digest, load_metadata(json.dumps(data).encode()).digest)
        normalized = json.loads(implicit.canonical)
        self.assertFalse(normalized["spec"]["enabled"])

    def test_scale_and_credentials_do_not_change_selected_release(self) -> None:
        target = fixture("target")
        baseline = load_metadata(json.dumps(target).encode())
        for key, value in (("replicas", 3), ("credentials", {"modelStorage": "other", "imageRegistry": "other"})):
            modified = copy.deepcopy(target)
            modified["spec"][key] = value
            result = load_metadata(json.dumps(modified).encode())
            self.assertNotEqual(baseline.digest, result.digest)
            self.assertEqual(
                json.loads(baseline.canonical)["spec"]["release"], json.loads(result.canonical)["spec"]["release"]
            )

    def test_release_changes_produce_new_identity(self) -> None:
        data = fixture("release-manual")
        original = load_metadata(json.dumps(data).encode()).digest
        for group, key, value in (
            ("metadata", "release", "r9"),
            ("spec", "models", [{"name": "toy", "versions": ["2"]}]),
            (
                "spec",
                "resourcesPerReplica",
                {
                    "requests": {"cpu": "300m", "memory": "128Mi"},
                    "limits": {"cpu": "1", "memory": "256Mi"},
                    "gpuCount": 0,
                },
            ),
        ):
            changed = copy.deepcopy(data)
            changed[group][key] = value
            self.assertNotEqual(original, load_metadata(json.dumps(changed).encode()).digest)
        data["spec"]["validation"]["digest"] = "sha256:" + hashlib.sha256(b"different synthetic evidence").hexdigest()
        self.assertNotEqual(original, load_metadata(json.dumps(data).encode()).digest)
        runtime = fixture("release-runtime-only")
        self.assertEqual(runtime["spec"]["lineage"]["candidate_digest"], data["spec"]["lineage"]["candidate_digest"])
        self.assertNotEqual(original, load_metadata(json.dumps(runtime).encode()).digest)

    def test_lineage_v1_vectors_and_real_fixture_bytes(self) -> None:
        vectors = fixture("hashes")["lineageV1"]
        candidate = ServingCandidateV1.model_validate(fixture("artifacts/candidate"))
        inventory = InventoryV1.model_validate(fixture("artifacts/inventory"))
        self.assertEqual(digest(candidate.model_dump(mode="json")), vectors["candidate"])
        self.assertEqual(inventory_digest(inventory), vectors["inventory"])
        self.assertEqual(candidate.plugin_references, ())
        weights = (FIXTURES / "artifacts/weights.txt").read_bytes()
        self.assertEqual(inventory.files[0].digest, "sha256:" + hashlib.sha256(weights).hexdigest())
        self.assertEqual(inventory.files[0].size, len(weights))
        for name in ("release-manual", "release-runtime-only", "release-security"):
            spec = fixture(name)["spec"]
            for key in ("lineageManifest", "validation", "security"):
                if key in spec:
                    ref = spec[key]
                    self.assertEqual(
                        ref["digest"], "sha256:" + hashlib.sha256((FIXTURES / ref["uri"]).read_bytes()).hexdigest()
                    )

    def test_unknown_versions_kinds_fields_and_missing_discriminators(self) -> None:
        base = fixture("target")
        for key, value in (("apiVersion", "serving.triton-control/v9"), ("kind", "Deployment"), ("extra", 1)):
            data = copy.deepcopy(base)
            data[key] = value
            self.reject(data)
        for key in ("apiVersion", "kind"):
            data = copy.deepcopy(base)
            del data[key]
            self.reject(data)
        for location in (base["metadata"], base["spec"], base["spec"]["credentials"], base["spec"]["rollout"]):
            location["unknown"] = "not-supported"
            self.reject(base)
            del location["unknown"]
        self.reject({"apiVersion": "v1", "kind": "Secret", "data": {}})

    def test_target_replicas_and_rollout(self) -> None:
        for value in (0, -1, True, 1.0, 1.5, "3", 1_000_001):
            data = fixture("target")
            data["spec"]["replicas"] = value
            self.reject(data)
        for rollout in (
            {"maxSurge": 0, "maxUnavailable": 0},
            {"maxSurge": 1, "maxUnavailable": 2},
            {"maxSurge": -1, "maxUnavailable": 1},
            {"maxSurge": "25%", "maxUnavailable": 0},
        ):
            data = fixture("target")
            data["spec"]["rollout"] = rollout
            self.reject(data)

    def test_resources_and_model_selections(self) -> None:
        for group, key, value in (
            ("requests", "cpu", "0"),
            ("requests", "cpu", "2000m"),
            ("limits", "memory", "1"),
            ("requests", "memory", "0Mi"),
        ):
            data = fixture("release-manual")
            data["spec"]["resourcesPerReplica"][group][key] = value
            self.reject(data)
        data = fixture("release-manual")
        data["spec"]["resourcesPerReplica"]["gpuCount"] = -1
        self.reject(data)
        for models in (
            [],
            [{"name": "toy", "versions": ["1", "1"]}],
            [{"name": "../toy", "versions": ["1"]}],
            [{"name": "toy", "versions": ["1"]}] * 2,
        ):
            data = fixture("release-manual")
            data["spec"]["models"] = models
            self.reject(data)

    def test_evidence_is_acyclic_and_matches_artifacts(self) -> None:
        for key in ("image", "inventoryDigest", "preparedInventoryDigest", "materializerImage", "releaseDigest"):
            data = fixture("release-manual")
            data["spec"]["validation"]["subjects"][key] = "sha256:" + hashlib.sha256(b"unrelated").hexdigest()
            self.reject(data)
        data = fixture("release-manual")
        data["spec"]["lineage"]["runtime_image"] = "registry.example.invalid/synthetic/runtime:latest"
        self.reject(data)
        data = fixture("release-manual")
        data["spec"]["lineage"]["replaces_release"] = data["spec"]["lineage"]["origin"]
        self.reject(data)
        for key in ("approved", "passed", "releaseDigest"):
            data = fixture("release-manual")
            data["spec"]["validation"][key] = True
            self.reject(data)

    def test_prepared_inventory_requires_materializer(self) -> None:
        data = fixture("release-manual")
        prepared = "sha256:" + hashlib.sha256(b"synthetic prepared bytes").hexdigest()
        data["spec"]["repository"]["preparedInventoryDigest"] = prepared
        data["spec"]["validation"]["subjects"]["preparedInventoryDigest"] = prepared
        self.reject(data)
        materializer = "sha256:" + hashlib.sha256(b"synthetic materializer descriptor").hexdigest()
        data["spec"]["repository"]["materializerImage"] = f"registry.example.invalid/synthetic/init@{materializer}"
        data["spec"]["validation"]["subjects"]["materializerImage"] = materializer
        load_metadata(json.dumps(data).encode())

    def test_sources_have_explicit_scope_and_no_approval_bypass(self) -> None:
        for key, value in (
            ("allowedTargets", ["*"]),
            ("allowedTargets", []),
            ("allowedTargets", ["toy", "toy"]),
            ("intervalSeconds", 0),
            ("intervalSeconds", True),
            ("intervalSeconds", 2_592_001),
            ("approval", {"mode": "automatic"}),
            ("approval", {"mode": "manual", "approved": True}),
            ("enabled", "true"),
            ("readCredential", "secrets/production"),
        ):
            data = fixture("source-git")
            data["spec"][key] = value
            self.reject(data)
        data = fixture("source-s3")
        data["spec"]["location"]["repository"] = "https://example.invalid/repo"
        self.reject(data)

    def test_paths_and_uris_cannot_carry_credentials_or_traversal(self) -> None:
        for uri in (
            "https://user:password@example.invalid/a",
            "https://example.invalid/a?token=secret",
            "s3://bucket/a#secret",
            "s3://bucket/a/../b",
            "s3://bucket/%2e%2e/b",
            "file:///etc/passwd",
            "../release.json",
            "/absolute/file",
            "https://example.invalid:bad/a",
            "s3://bucket:9000/a",
        ):
            data = fixture("target")
            data["spec"]["release"]["uri"] = uri
            self.reject(data)
        for path in ("../target.yaml", "a//b", "a/./b", "$(cmd)", "a\\b"):
            data = fixture("source-git")
            data["spec"]["location"]["path"] = path
            self.reject(data)

    def test_bounded_parser_rejects_adversarial_documents(self) -> None:
        for raw in (
            b"x" * (MAX_BYTES + 1),
            b"\xff",
            b"",
            b"[]",
            b"null",
            b"x: 1\nx: 2",
            b'{"x":1,"x":2}',
            b'{"x":1,"\\u0078":2}',
            b"a: &a [1, 2]\nb: *a",
            b"a: *missing",
            b"a: !!python/object/apply:os.system [echo oops]",
            b"a: !!str 3",
            b"a: 1\n---\nb: 2",
            b"<<: {a: 1}",
            b"true: value",
            b"[a]: value",
            b"a: " + b"[" * 33 + b"1" + b"]" * 33,
            b"a: [" + b"1," * 10001 + b"1]",
            b"a: " + b"9" * 1000,
            b'{"a":NaN}',
            b'{"a":Infinity}',
            b'{"a":1e999}',
        ):
            with self.subTest(prefix=raw[:30]), self.assertRaises(MetadataError):
                load_metadata(raw)

    def test_yaml_scalar_rules_and_strict_json_grammar(self) -> None:
        raw = (FIXTURES / "source-git.yaml").read_text()
        for value in ("yes", "on", "2026-01-01", "1.0", "True", ".nan"):
            with self.subTest(value=value), self.assertRaises(MetadataError):
                load_metadata((raw + f"  enabled: {value}\n").encode(), format="yaml")
        with self.assertRaises(MetadataError):
            load_metadata(raw.encode(), format="json")
        with self.assertRaises(MetadataError):
            load_metadata(b'{"a":1,}', format="json")

    def test_loader_limits_fire_before_schema_validation(self) -> None:
        cases = (
            (b"x" * (MAX_BYTES + 1), "byte-limit"),
            (b"a: " + b"[" * 33 + b"1" + b"]" * 33, "structure-limit"),
            (b"a: [" + b"1," * 10001 + b"1]", "structure-limit"),
            (b"x: 1\nx: 2", "duplicate-key"),
            (b"x: &x []", "yaml-reference-or-tag"),
            (b"x: !!str value", "yaml-reference-or-tag"),
            (b"x: 1\n---\nx: 2", "single-document-required"),
        )
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaises(MetadataError) as raised:
                load_metadata(raw)
            self.assertEqual(raised.exception.code, code)
        raw = (FIXTURES / "target.json").read_bytes()
        padded = raw + b" " * (MAX_BYTES - len(raw))
        self.assertEqual(load_metadata(raw).digest, load_metadata(padded).digest)

    def test_semantic_validation_is_required_after_structural_validation(self) -> None:
        data = fixture("target")
        data["spec"]["rollout"] = {"maxSurge": 0, "maxUnavailable": 0}
        schema = json.loads((SCHEMAS / "ServingTarget.schema.json").read_text())
        self.assertTrue(Draft202012Validator(schema).is_valid(data))
        with self.assertRaises(MetadataError) as raised:
            load_metadata(json.dumps(data).encode())
        self.assertEqual(raised.exception.code, "semantic-invalid")

    def test_diagnostics_do_not_echo_secrets(self) -> None:
        secret = "DO-NOT-PRINT-THIS"
        for raw in (b"password: [" + secret.encode(), json.dumps({secret: secret}).encode()):
            with self.assertRaises(MetadataError) as raised:
                load_metadata(raw)
            self.assertNotIn(secret, str(raised.exception))
        data = fixture("target")
        data["spec"]["credentials"][secret] = secret
        with self.assertRaises(MetadataError) as raised:
            load_metadata(json.dumps(data).encode())
        self.assertNotIn(secret, str(raised.exception))

    def test_offline_validation_never_fetches_references(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            load_metadata((FIXTURES / "release-manual.json").read_bytes())

    def test_cli_batch_stdin_and_safe_failures(self) -> None:
        script = ROOT / "triton-backend/scripts/validate_serving_metadata.py"
        result = subprocess.run(
            [sys.executable, str(script), str(FIXTURES / "target.json"), "-"],
            input=(FIXTURES / "source-git.yaml").read_bytes(),
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 2)
        bad = subprocess.run(
            [sys.executable, str(script), "-", "/missing/DO-NOT-PRINT-THIS"],
            input=b"password: DO-NOT-PRINT-THIS",
            capture_output=True,
            check=False,
        )
        self.assertEqual(bad.returncode, 1)
        self.assertNotIn(b"DO-NOT-PRINT-THIS", bad.stderr)
        self.assertEqual(len(bad.stderr.splitlines()), 2)

    def test_exports_and_fixtures_do_not_drift(self) -> None:
        for name in ("export_serving_metadata_schema.py", "generate_serving_metadata_fixtures.py"):
            result = subprocess.run(
                [sys.executable, str(ROOT / "triton-backend/scripts" / name), "--check"],
                capture_output=True,
                check=False,
                env=os.environ.copy(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
