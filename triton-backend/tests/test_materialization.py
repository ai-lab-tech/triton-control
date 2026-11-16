"""Immutable inventories, deterministic normalization, recovery and provisioning gates."""

import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from pydantic import ValidationError

from app.core.canonical import inventory_digest
from app.schemas.materialization import MaterializationPlan, StorageBinding
from app.schemas.serving_metadata import ServingRelease
from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.filesystem import inventory
from app.services.materialization.kubernetes import manifests
from app.services.materialization.normalization import MaterializationError
from app.services.materialization.qualification import require_qualified
from app.services.materialization.runtime import materialize, receipt_for
from app.services.materialization.s3 import VersionedS3
from tests.materialization_fixtures import FILES, IMAGE, DirectoryFixture, plan, policy


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary) / "output"
        self.plan = plan()
        self.source = DirectoryFixture()

    def run_materializer(self, source=None, specification=None):
        return materialize(specification or self.plan, source or self.source, self.root, expected_image=IMAGE)

    def test_exact_source_prepared_inventories_receipt_and_restart(self):
        receipt = self.run_materializer()
        self.assertEqual(receipt, receipt_for(self.plan))
        self.assertEqual(
            inventory_digest(inventory(self.root / "ready/models", CaptureLimits())), receipt.prepared_inventory_digest
        )
        self.assertEqual((self.root / "ready/source/1/model.json").read_bytes(), FILES["1/model.json"])
        self.assertEqual(
            (self.root / "ready/models/iris/1/model.json").read_bytes(), b'{"model":"/models/iris/1/weights"}\n'
        )
        self.assertFalse((self.root / "ready/models/receipt.json").exists())
        unavailable = MagicMock()
        unavailable.download.side_effect = AssertionError("complete retries must verify retained bytes")
        self.assertEqual(self.run_materializer(unavailable), receipt)
        unavailable.download.assert_not_called()

    def test_corrupt_missing_and_extra_files_never_publish(self):
        for scenario in ("corrupt", "missing", "extra", "symlink", "hardlink"):

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
            with self.subTest(scenario=scenario), self.assertRaises((MaterializationError, ValueError)):
                self.run_materializer(source)
            self.assertFalse((self.root / "ready").exists())
            self.assertFalse(list(self.root.glob(".staging-*")))

    def test_interrupted_download_and_abandoned_staging_are_recoverable(self):
        def interrupted(spec, destination, limits):
            (destination / "partial").write_bytes(b"partial")
            raise OSError("connection lost")

        with self.assertRaises(OSError):
            self.run_materializer(MagicMock(download=interrupted))
        abandoned = self.root / ".staging-crashed"
        abandoned.mkdir()
        (abandoned / "partial").write_bytes(b"partial")
        self.run_materializer()
        self.assertFalse(abandoned.exists())

    def test_changed_prepared_inventory_or_normalization_blocks_finalization(self):
        for update in ("prepared", "normalization"):
            payload = self.plan.model_dump(mode="json")
            if update == "prepared":
                payload["prepared_inventory"]["files"][0]["digest"] = "sha256:" + "0" * 64
            else:
                payload["normalization"]["rewrites"][0]["relative"] = "absent"
            changed = MaterializationPlan.model_validate(payload)
            with self.subTest(update=update), self.assertRaises(MaterializationError):
                self.run_materializer(specification=changed)
            self.assertFalse((self.root / "ready").exists())

    def test_changed_helper_is_rejected_before_reading_or_writing(self):
        source = MagicMock()
        with self.assertRaises(MaterializationError):
            materialize(self.plan, source, self.root, expected_image="other@sha256:" + "0" * 64)
        source.download.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_retained_receipt_and_bytes_are_reverified(self):
        self.run_materializer()
        target = self.root / "ready/models/iris/1/weights/model.bin"
        target.chmod(0o644)
        target.write_bytes(b"corrupt retained bytes")
        with self.assertRaises(MaterializationError):
            self.run_materializer()

    def test_concurrent_materializer_does_not_write_or_remove_other_staging(self):
        self.root.mkdir()
        lock = (self.root / ".materializer.lock").open("w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source = MagicMock()
        with self.assertRaises(BlockingIOError):
            self.run_materializer(source)
        source.download.assert_not_called()

    def test_unexpected_destination_or_symlink_is_not_followed(self):
        self.root.mkdir()
        (self.root / "ready").symlink_to(self.root.parent)
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
            payload = self.plan.model_dump(mode="json")
            mutate(payload)
            with self.assertRaises(ValidationError):
                MaterializationPlan.model_validate(payload)

    def test_s3_requires_exact_version_content_and_key_set(self):
        client = MagicMock()
        listing = [{"Key": "repository/" + path} for path in FILES]
        client.get_paginator.return_value.paginate.return_value = [{"Contents": listing}]

        def get(**kwargs):
            self.assertEqual(kwargs["VersionId"], "v1")
            data = FILES[kwargs["Key"].removeprefix("repository/")]
            return {"Body": io.BytesIO(data), "VersionId": "v1", "ContentLength": len(data)}

        client.get_object.side_effect = get
        receipt = self.run_materializer(VersionedS3(client, "bucket", "repository"))
        self.assertEqual(receipt, receipt_for(self.plan))
        self.assertFalse(client.put_object.called)

    def test_s3_wrong_returned_version_and_extra_files_fail(self):
        for scenario in ("version", "extra", "missing", "unsafe"):
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
            with self.subTest(scenario=scenario), self.assertRaises(Exception):
                self.run_materializer(VersionedS3(client, "bucket", "repository"))
            self.assertFalse((self.root / "ready").exists())

    def test_offline_preparation_and_schema_export(self):
        source = Path(self.temporary) / "source"
        source.mkdir()
        self.source.download(self.plan, source, CaptureLimits())
        spec = Path(self.temporary) / "normalization.json"
        spec.write_text(self.plan.normalization.model_dump_json())
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.services.materialization.cli",
                "prepare",
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
        self.assertEqual(
            json.loads(result.stdout)["prepared_inventory_digest"], inventory_digest(self.plan.prepared_inventory)
        )
        subprocess.run([sys.executable, "scripts/export_materialization_schema.py", "--check"], check=True)


class MaterializerPodTests(unittest.TestCase):
    def release(self):
        path = Path(__file__).resolve().parents[2] / "contracts/serving/v1alpha1/fixtures/release-manual.json"
        payload = json.loads(path.read_text())
        spec = payload["spec"]
        repository = spec["repository"]
        repository.update(
            uri="s3://models/repository",
            inventoryDigest=inventory_digest(plan().source_inventory),
            preparedInventoryDigest=inventory_digest(plan().prepared_inventory),
            materializerImage=IMAGE,
        )
        for name in ("validation", "security"):
            if spec.get(name):
                spec[name]["subjects"].update(
                    inventoryDigest=repository["inventoryDigest"],
                    preparedInventoryDigest=repository["preparedInventoryDigest"],
                    materializerImage=IMAGE.split("@", 1)[1],
                )
        return ServingRelease.model_validate(payload)

    def test_shared_path_uses_init_readonly_models_and_no_mutable_runtime(self):
        docs = manifests(
            self.release(),
            plan(),
            policy(),
            StorageBinding(bucket="models", prefix="repository", secret_name="read-only-models"),
            name="iris",
            namespace="test",
        )
        spec = docs[1]["spec"]
        self.assertEqual(spec["initContainers"][0]["image"], IMAGE)
        runtime = spec["containers"][0]
        self.assertEqual(runtime["command"], ["tritonserver"])
        self.assertIn("--model-control-mode=none", runtime["args"])
        self.assertNotIn("env", runtime)
        self.assertTrue(runtime["volumeMounts"][0]["readOnly"])
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(len(spec["containers"]), 1)
        self.assertNotIn("pip install", json.dumps(docs))
        self.assertNotIn("repository-poll", json.dumps(docs))

    def test_no_missing_digest_unqualified_helper_or_cross_binding(self):
        for field in ("image", "platform"):
            payload = policy().model_dump(mode="json")
            payload[field] = IMAGE.replace("fixture.invalid", "other.invalid") if field == "image" else "linux/arm64"
            from app.schemas.materialization import MaterializerPolicy

            with self.assertRaises(MaterializationError):
                require_qualified(MaterializerPolicy.model_validate(payload))
        result = policy().model_dump(mode="json")
        result["qualification"]["checks"]["readonly_mount"] = False
        with self.assertRaises(MaterializationError):
            require_qualified(MaterializerPolicy.model_validate(result))
        with self.assertRaises(MaterializationError):
            manifests(
                self.release(),
                plan(),
                policy(),
                StorageBinding(bucket="other", prefix="repository", secret_name="read-only"),
                name="iris",
                namespace="test",
            )
