"""Real S3-compatible capture/retention, opt in against a disposable MinIO service."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import boto3
from botocore.config import Config

from app.exceptions import BadRequestError, ConflictError, ServiceUnavailableError
from app.services.candidates.artifacts import CaptureLimits, S3ArtifactStore, S3Source
from app.services.candidates.importer import get_import
from tests import test_candidate_import as fixtures
from tests.test_candidate_s3 import upload_source

ENDPOINT = os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT", "")


@unittest.skipUnless(ENDPOINT, "Set CANDIDATE_IMPORT_TEST_S3_ENDPOINT for real S3 tests")
class CandidateS3IntegrationTests(fixtures.CandidateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            aws_access_key_id=os.getenv("CANDIDATE_IMPORT_TEST_S3_ACCESS_KEY", "candidate_test"),
            aws_secret_access_key=os.getenv("CANDIDATE_IMPORT_TEST_S3_SECRET_KEY", "candidate_test_password"),
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=10),
        )
        self.bucket = "candidate-test-" + uuid4().hex
        self.client.create_bucket(Bucket=self.bucket)
        self.addCleanup(self.cleanup_bucket)
        self.client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
        upload_source(self.client, self.source, bucket=self.bucket)
        self.provider = S3Source(self.client, self.bucket, "prepared")
        self.output = S3ArtifactStore(self.client, self.bucket, "snapshots")

    def cleanup_bucket(self):
        paginator = self.client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=self.bucket):
            objects = [
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for kind in ("Versions", "DeleteMarkers")
                for item in page.get(kind, [])
            ]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
        self.client.delete_bucket(Bucket=self.bucket)
        self.client.close()

    def test_real_versioned_import_is_readable_and_repeatable_without_mlflow(self):
        result = self.do_import(source=self.provider, store=self.output)
        self.assertEqual(result.candidate.plugin_references, ())
        self.assertEqual(get_import(self.session, self.engineer, result.candidate.origin, self.output), result)
        self.assertEqual(self.do_import(source=self.provider, store=self.output), result)
        self.assertTrue(all(item["version_id"] for item in result.source_receipt["objects"].values()))

    def test_real_destination_overwrite_does_not_change_pinned_candidate(self):
        result = self.do_import(source=self.provider, store=self.output)
        item = result.artifacts.reference["objects"][0]
        self.client.put_object(Bucket=self.bucket, Key=item["key"], Body=b"later replacement")
        self.output.verify(result.artifacts, result.inventory, result.metadata_inventory, CaptureLimits())
        self.assertEqual(self.counts(), (1, 1))

    def test_real_changed_source_requires_new_candidate_and_preserves_previous_snapshot(self):
        first = self.do_import(source=self.provider, store=self.output)
        self.client.put_object(Bucket=self.bucket, Key="prepared/iris/1/model.onnx", Body=b"changed fixture")
        with self.assertRaises(ConflictError):
            self.do_import(source=self.provider, store=self.output)
        second = self.do_import(fixtures.request("candidate-2"), source=self.provider, store=self.output)
        self.assertNotEqual(first.candidate.output_inventory, second.candidate.output_inventory)
        self.output.verify(first.artifacts, first.inventory, first.metadata_inventory, CaptureLimits())

    def test_real_source_change_during_download_is_detected(self):
        original = self.client.get_object
        changed = False

        def download(**kwargs):
            nonlocal changed
            response = original(**kwargs)
            if not changed:
                changed = True
                self.client.put_object(Bucket=self.bucket, Key=kwargs["Key"], Body=b"changed during capture")
            return response

        with patch.object(self.client, "get_object", side_effect=download), self.assertRaises(ConflictError):
            self.do_import(source=self.provider, store=self.output)
        self.assertEqual(self.counts(), (0, 0))

    def test_real_nonversioned_source_still_uses_conditionals_and_local_snapshot(self):
        self.client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Suspended"})
        upload_source(self.client, self.source, bucket=self.bucket, prefix="unversioned/")
        result = self.do_import(source=S3Source(self.client, self.bucket, "unversioned"))
        self.assertTrue(all(item["version_id"] in (None, "null") for item in result.source_receipt["objects"].values()))

    def test_real_nonversioned_destination_is_not_accepted(self):
        self.client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Suspended"})
        with self.assertRaises(BadRequestError):
            self.do_import(source=self.provider, store=self.output)
        self.assertEqual(self.counts(), (0, 0))

    def test_deleted_retained_version_blocks_read_without_rewriting_candidate(self):
        result = self.do_import(source=self.provider, store=self.output)
        item = result.artifacts.reference["objects"][0]
        self.client.delete_object(Bucket=self.bucket, Key=item["key"], VersionId=item["version_id"])
        with self.assertRaises(ServiceUnavailableError):
            get_import(self.session, self.engineer, result.candidate.origin, self.output)
        self.assertEqual(self.counts(), (1, 1))
