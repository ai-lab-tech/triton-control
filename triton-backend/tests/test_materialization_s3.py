"""Materialize a real neutral-import receipt from versioned MinIO storage."""

import os
import tempfile
import unittest
from pathlib import Path

from app.services.candidates.artifacts import CaptureLimits
from app.services.materialization.imports import from_import
from app.services.materialization.runtime import materialize
from app.services.materialization.s3 import VersionedS3
from tests import test_candidate_import_s3_integration as integration
from tests.materialization_fixtures import IMAGE, LIMITS


@unittest.skipUnless(os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT"), "Requires real versioned S3")
class MaterializerS3Tests(integration.CandidateS3IntegrationTests):
    def captured(self):
        imported = self.do_import(source=self.provider, store=self.output)
        desired = from_import(imported, IMAGE)
        reference = imported.artifacts.reference
        source = VersionedS3(self.client, self.bucket, reference["prefix"] + "/repository")
        item = next(item for item in reference["objects"] if item["area"] == "repository")
        return desired, source, reference, item

    def test_import_adapter_uses_exact_retained_versions(self):
        # Arrange
        desired, source, _, item = self.captured()
        self.client.put_object(Bucket=self.bucket, Key=item["key"], Body=b"overwritten latest object")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "materialized"
            # Act
            receipt = materialize(desired, source, root, expected_image=IMAGE, limits=LIMITS)
            # Assert
            self.assertEqual(receipt.source_inventory_digest, receipt.prepared_inventory_digest)

    def test_real_s3_materialization_can_be_reopened(self):
        # Arrange
        desired, source, _, _ = self.captured()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "materialized"
            receipt = materialize(desired, source, root, expected_image=IMAGE, limits=LIMITS)
            # Act
            repeated = materialize(desired, source, root, expected_image=IMAGE, limits=LIMITS)
            # Assert
            self.assertEqual(repeated, receipt)

    def test_extra_s3_object_blocks_publication(self):
        # Arrange
        desired, source, reference, _ = self.captured()
        self.client.put_object(Bucket=self.bucket, Key=reference["prefix"] + "/repository/extra", Body=b"extra")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "materialized"
            # Act / Assert
            with self.assertRaises(ValueError):
                materialize(desired, source, root, expected_image=IMAGE, limits=LIMITS)
            self.assertFalse((root / "ready").exists())

    def test_deleted_pinned_s3_version_blocks_download(self):
        # Arrange
        from botocore.exceptions import ClientError
        desired, source, _, item = self.captured()
        self.client.put_object(Bucket=self.bucket, Key=item["key"], Body=b"replacement")
        self.client.delete_object(Bucket=self.bucket, Key=item["key"], VersionId=item["version_id"])
        with tempfile.TemporaryDirectory() as folder:
            # Act / Assert
            with self.assertRaises(ClientError):
                source.download(desired, Path(folder), CaptureLimits())
