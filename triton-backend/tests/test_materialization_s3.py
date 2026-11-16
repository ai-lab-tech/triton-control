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
from tests.materialization_fixtures import IMAGE


@unittest.skipUnless(os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT"), "Requires real versioned S3")
class MaterializerS3Tests(integration.CandidateS3IntegrationTests):
    def test_import_adapter_and_exact_retained_versions(self):
        imported = self.do_import(source=self.provider, store=self.output)
        desired = from_import(imported, IMAGE)
        reference = imported.artifacts.reference
        source = VersionedS3(self.client, self.bucket, reference["prefix"] + "/repository")
        item = next(item for item in reference["objects"] if item["area"] == "repository")
        self.client.put_object(Bucket=self.bucket, Key=item["key"], Body=b"overwritten latest object")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "materialized"
            receipt = materialize(desired, source, root, expected_image=IMAGE)
            self.assertEqual(receipt.source_inventory_digest, receipt.prepared_inventory_digest)
            self.assertEqual(materialize(desired, source, root, expected_image=IMAGE), receipt)
            self.client.put_object(Bucket=self.bucket, Key=reference["prefix"] + "/repository/extra", Body=b"extra")
            with self.assertRaises(ValueError):
                materialize(desired, source, Path(folder) / "extra", expected_image=IMAGE)
        self.client.delete_object(Bucket=self.bucket, Key=reference["prefix"] + "/repository/extra")
        self.client.delete_object(Bucket=self.bucket, Key=item["key"], VersionId=item["version_id"])
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(Exception):
            source.download(desired, Path(folder), CaptureLimits())
