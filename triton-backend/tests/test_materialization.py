"""Immutable inventories, deterministic normalization, recovery and provisioning gates."""

import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

from pydantic import ValidationError

from app.core.canonical import inventory_digest
from app.exceptions import BadRequestError
from app.schemas.materialization import MaterializationPlan, StorageBinding
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.filesystem import inventory
from app.services.materialization.kubernetes import manifests
from app.services.materialization.normalization import MaterializationError
from app.services.materialization.qualification import require_qualified
from app.services.materialization.runtime import materialize, receipt_for
from app.services.materialization.s3 import VersionedS3
from tests.materialization_fixtures import FILES, IMAGE, LIMITS, DirectoryFixture, plan, policy, serving_release


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary) / "output"
        self.plan = plan()
        self.source = DirectoryFixture()

    def run_materializer(self, source=None, specification=None):
        return materialize(
            specification or self.plan, source or self.source, self.root, expected_image=IMAGE, limits=LIMITS
        )

    def test_exact_source_prepared_inventories_and_receipt(self):
        # Arrange
        expected = receipt_for(self.plan)
        # Act
        receipt = self.run_materializer()
        # Assert
        self.assertEqual(receipt, expected)
        self.assertEqual(
            inventory_digest(inventory(self.root / "ready/models", CaptureLimits())), receipt.prepared_inventory_digest
        )
        self.assertEqual((self.root / "ready/source/1/model.json").read_bytes(), FILES["1/model.json"])
        self.assertEqual(
            (self.root / "ready/models/iris/1/model.json").read_bytes(), b'{"model":"/models/iris/1/weights"}\n'
        )
        self.assertFalse((self.root / "ready/models/receipt.json").exists())

    def test_restart_reuses_verified_bytes_without_source_access(self):
        # Arrange
        receipt = self.run_materializer()
        unavailable = MagicMock()
        unavailable.download.side_effect = OSError("storage unavailable")
        # Act
        restarted = self.run_materializer(unavailable)
        # Assert
        self.assertEqual(restarted, receipt)
        unavailable.download.assert_not_called()

    def test_corrupt_missing_and_extra_files_never_publish(self):
        for scenario in ("corrupt", "missing", "extra", "symlink", "hardlink"):
            # Arrange

            def download(spec, destination, limits):
                self.source.download(spec, destination, limits)
                target = destination / "1/weights/model.bin"
                if scenario == "corrupt":
                    target.write_bytes(b"different bytes")
                elif scenario == "missing":
                    target.unlink()
                elif scenario == "extra":
                    (destination / "untracked").write_bytes(b"extra")
                elif scenario == "symlink":
                    target.unlink()
                    target.symlink_to("/etc/passwd")
                else:
                    os.link(target, destination / "hardlink")

            source = MagicMock(download=download)
            # Act / Assert
            with (
                self.subTest(scenario=scenario),
                self.assertRaises((MaterializationError, BadRequestError, ValueError)),
            ):
                self.run_materializer(source)
            self.assertFalse((self.root / "ready").exists())
            self.assertFalse(list(self.root.glob(".staging-*")))

    def test_interrupted_download_never_publishes_partial_bytes(self):
        # Arrange
        def interrupted(spec, destination, limits):
            (destination / "partial").write_bytes(b"partial")
            raise OSError("connection lost")

        source = MagicMock(download=interrupted)
        # Act / Assert
        with self.assertRaises(OSError):
            self.run_materializer(source)
        self.assertFalse((self.root / "ready").exists())
        self.assertFalse(list(self.root.glob(".staging-*")))

    def test_abandoned_staging_is_removed_before_recovery(self):
        # Arrange
        abandoned = self.root / ".staging-crashed"
        abandoned.mkdir(parents=True)
        (abandoned / "partial").write_bytes(b"partial")
        # Act
        receipt = self.run_materializer()
        # Assert
        self.assertFalse(abandoned.exists())
        self.assertEqual(receipt, receipt_for(self.plan))

    def test_changed_prepared_inventory_or_normalization_blocks_finalization(self):
        for update in ("prepared", "normalization"):
            # Arrange
            payload = self.plan.model_dump(mode="json")
            if update == "prepared":
                payload["prepared_inventory"]["files"][0]["digest"] = "sha256:" + "0" * 64
            else:
                payload["normalization"]["rewrites"][0]["relative"] = "absent"
            changed = MaterializationPlan.model_validate(payload)
            # Act / Assert
            with self.subTest(update=update), self.assertRaises(MaterializationError):
                self.run_materializer(specification=changed)
            self.assertFalse((self.root / "ready").exists())

    def test_changed_helper_is_rejected_before_reading_or_writing(self):
        # Arrange
        source = MagicMock()
        # Act / Assert
        with self.assertRaises(MaterializationError):
            materialize(self.plan, source, self.root, expected_image="other@sha256:" + "0" * 64, limits=LIMITS)
        source.download.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_retained_receipt_and_bytes_are_reverified(self):
        # Arrange
        self.run_materializer()
        target = self.root / "ready/models/iris/1/weights/model.bin"
        target.chmod(0o644)
        target.write_bytes(b"corrupt retained bytes")
        # Act / Assert
        with self.assertRaises(MaterializationError):
            self.run_materializer()

    def test_concurrent_materializer_does_not_write_or_remove_other_staging(self):
        # Arrange
        self.root.mkdir()
        lock = (self.root / ".materializer.lock").open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = MagicMock()
        # Act / Assert
        with self.assertRaises(BlockingIOError):
            self.run_materializer(source)
        source.download.assert_not_called()

    def test_unexpected_destination_or_symlink_is_not_followed(self):
        # Arrange
        self.root.mkdir()
        (self.root / "ready").symlink_to(self.root.parent)
        # Act / Assert
        with self.assertRaises((OSError, ValueError)):
            self.run_materializer()

    def test_rejects_mutable_helper_versions_unsafe_paths_and_implicit_transforms(self):
        for mutate in (
            lambda d: d.update(materializer_image="fixture.invalid/helper:latest"),
            lambda d: d["objects"][0].update(version_id="null"),
            lambda d: d["objects"][0].update(path="../escape"),
            lambda d: d["normalization"].update(version="unknown/v2"),
            lambda d: d["normalization"].update(version="identity/v1"),
            lambda d: d["normalization"]["rewrites"][0].update(target="../../escape"),
            lambda d: d["normalization"]["rewrites"][0].update(target="iris/not-in-inventory"),
        ):
            # Arrange
            payload = self.plan.model_dump(mode="json")
            mutate(payload)
            # Act / Assert
            with self.assertRaises(ValidationError):
                MaterializationPlan.model_validate(payload)

    def test_s3_requires_exact_version_content_and_key_set(self):
        # Arrange
        client = MagicMock()
        listing = [{"Key": "repository/" + path} for path in FILES]
        client.get_paginator.return_value.paginate.return_value = [{"Contents": listing}]

        def get(**kwargs):
            data = FILES[kwargs["Key"].removeprefix("repository/")]
            return {"Body": io.BytesIO(data), "VersionId": "v1", "ContentLength": len(data)}

        client.get_object.side_effect = get
        # Act
        receipt = self.run_materializer(VersionedS3(client, "bucket", "repository"))
        # Assert
        self.assertEqual(receipt, receipt_for(self.plan))
        self.assertFalse(client.put_object.called)
        self.assertEqual(len(client.get_object.call_args_list), len(FILES))
        self.assertTrue(all(call.kwargs["VersionId"] == "v1" for call in client.get_object.call_args_list))

    def test_s3_wrong_returned_version_and_extra_files_fail(self):
        for scenario in ("version", "extra", "missing", "unsafe"):
            # Arrange
            client = MagicMock()
            listing = [{"Key": "repository/" + path} for path in FILES]
            if scenario == "extra":
                listing.append({"Key": "repository/extra"})
            if scenario == "missing":
                listing.pop()
            if scenario == "unsafe":
                listing.append({"Key": "repository/../escape"})
            client.get_paginator.return_value.paginate.return_value = [{"Contents": listing}]
            data = next(iter(FILES.values()))
            client.get_object.return_value = {
                "Body": io.BytesIO(data),
                "VersionId": "different",
                "ContentLength": len(data),
            }
            # Act / Assert
            with (
                self.subTest(scenario=scenario),
                self.assertRaises((MaterializationError, BadRequestError, ValueError)),
            ):
                self.run_materializer(VersionedS3(client, "bucket", "repository"))
            self.assertFalse((self.root / "ready").exists())

    def test_offline_preparation_matches_runtime_inventory(self):
        # Arrange
        source = Path(self.temporary) / "source"
        source.mkdir()
        self.source.download(self.plan, source, CaptureLimits())
        spec = Path(self.temporary) / "normalization.json"
        spec.write_text(self.plan.normalization.model_dump_json())
        budget = Path(self.temporary) / "limits.json"
        budget.write_text(json.dumps(asdict(LIMITS)))
        # Act
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.services.materialization.cli",
                "prepare",
                "--limits",
                str(budget),
                "--source",
                str(source),
                "--normalization",
                str(spec),
                "--output",
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        # Assert
        self.assertEqual(
            json.loads(result.stdout)["prepared_inventory_digest"], inventory_digest(self.plan.prepared_inventory)
        )

    def test_exported_schema_matches_contract(self):
        # Arrange
        command = [sys.executable, "scripts/export_materialization_schema.py", "--check"]
        # Act
        result = subprocess.run(command, capture_output=True, text=True)
        # Assert
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


class MaterializerPodTests(unittest.TestCase):
    def test_shared_path_uses_init_readonly_models_and_no_mutable_runtime(self):
        # Arrange
        release, desired, qualified = serving_release(), plan(), policy()
        binding = StorageBinding(bucket="models", prefix="repository", secret_name="read-only-models")
        # Act
        docs = manifests(
            release,
            desired,
            qualified,
            binding,
            name="iris",
            namespace="test",
            limits=LIMITS,
        )
        # Assert
        spec = docs[1]["spec"]
        self.assertEqual(spec["initContainers"][0]["image"], IMAGE)
        runtime = spec["containers"][0]
        self.assertEqual(runtime["command"], ["tritonserver"])
        self.assertIn("--model-control-mode=none", runtime["args"])
        self.assertNotIn("env", runtime)
        self.assertTrue(runtime["volumeMounts"][0]["readOnly"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["containers"][1]["image"], IMAGE)
        self.assertNotIn("env", spec["containers"][1])
        self.assertNotIn("pip install", json.dumps(docs))
        self.assertNotIn("repository-poll", json.dumps(docs))

    def test_new_release_on_same_plan_gets_distinct_immutable_readiness_expectations(self):
        from app.schemas.serving_metadata import StartupModel
        from app.services.materialization.runtime import receipt_for

        release = serving_release()
        spec = release.spec.model_copy(update={"models": (StartupModel(name="iris", versions=("1", "2")),)})
        binding = StorageBinding(bucket="models", prefix="repository", secret_name="read-only-models")
        config, pod = manifests(
            release.model_copy(update={"spec": spec}),
            plan(),
            policy(),
            binding,
            name="iris",
            namespace="test",
            limits=LIMITS,
        )
        original, _ = manifests(release, plan(), policy(), binding, name="iris", namespace="test", limits=LIMITS)
        expected = json.loads(config["data"]["readiness.json"])
        self.assertTrue(config["immutable"])
        self.assertNotEqual(original["metadata"]["name"], config["metadata"]["name"])
        self.assertEqual(expected["models"], [{"name": "iris", "versions": ["1", "2"]}])
        self.assertEqual(expected["receipt"], receipt_for(plan()).model_dump(mode="json"))
        self.assertEqual(expected["release"], pod["metadata"]["annotations"]["triton-control/release-digest"])

    def test_qualification_subject_mismatch_is_rejected(self):
        for field in ("image", "platform"):
            # Arrange
            from app.schemas.materialization import MaterializerPolicy

            payload = policy().model_dump(mode="json")
            payload[field] = IMAGE.replace("fixture.invalid", "other.invalid") if field == "image" else "linux/arm64"
            request = MaterializerPolicy.model_validate(payload)
            # Act / Assert
            with self.subTest(field=field), self.assertRaises(MaterializationError):
                require_qualified(request)

    def test_failed_qualification_is_rejected(self):
        # Arrange
        from app.schemas.materialization import MaterializerPolicy

        payload = policy().model_dump(mode="json")
        payload["qualification"]["checks"]["readonly_mount"] = False
        request = MaterializerPolicy.model_validate(payload)
        # Act / Assert
        with self.assertRaises(MaterializationError):
            require_qualified(request)

    def test_cross_binding_is_rejected(self):
        # Arrange
        binding = StorageBinding(bucket="other", prefix="repository", secret_name="read-only")
        # Act / Assert
        with self.assertRaises(MaterializationError):
            manifests(serving_release(), plan(), policy(), binding, name="iris", namespace="test", limits=LIMITS)
