"""Real MinIO version pinning, uncertain-write recovery and retention behavior."""

import io
import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config

from app.exceptions import BadRequestError
from app.services.builds.contexts_archive import prepare
from app.services.builds.contexts_storage import ContextStore
from tests.test_build_contexts import archive, request

ENDPOINT = os.getenv("BUILD_CONTEXT_TEST_S3_ENDPOINT", "")


@unittest.skipUnless(ENDPOINT, "Set BUILD_CONTEXT_TEST_S3_ENDPOINT for real MinIO tests")
class BuildContextS3Tests(unittest.TestCase):
    def setUp(self):
        self.client = boto3.client(
            "s3", endpoint_url=ENDPOINT, aws_access_key_id=os.environ["BUILD_CONTEXT_TEST_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["BUILD_CONTEXT_TEST_S3_SECRET_KEY"], region_name="us-east-1",
            config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1}),
        )
        self.bucket = "context-test-" + uuid4().hex
        self.client.create_bucket(Bucket=self.bucket)
        self.store = ContextStore(self.client, self.bucket)
        self.addCleanup(self.cleanup)
        raw = archive()
        self.output = io.BytesIO()
        with tempfile.TemporaryDirectory() as temp:
            self.manifest = prepare(io.BytesIO(raw), Path(temp), self.output, request(raw), 10485760)
        self.key = "builds/context-one/attempt/context.tar.gz"

    def cleanup(self):
        self.store.delete_prefix("")
        self.client.delete_bucket(Bucket=self.bucket)
        self.client.close()

    def versioning(self):
        self.client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})

    def test_unversioned_bucket_rejected_before_write(self):
        with self.assertRaises(BadRequestError):
            self.store.put(self.key, self.output, self.manifest)
        self.assertEqual(self.client.list_objects_v2(Bucket=self.bucket).get("KeyCount"), 0)

    def test_verified_version_survives_overwrite_and_recovers_unacknowledged_put(self):
        self.versioning()
        version = self.store.put(self.key, self.output, self.manifest)
        self.assertEqual(self.store.recover(self.key, self.manifest), version)
        self.client.put_object(Bucket=self.bucket, Key=self.key, Body=b"changed live object")
        self.store.verify(self.key, version, self.manifest)
        self.client.delete_object(Bucket=self.bucket, Key=self.key, VersionId=version)
        with self.assertRaises(BadRequestError):
            self.store.verify(self.key, version, self.manifest)

    def test_corruption_never_passes_verification(self):
        self.versioning()
        version = self.client.put_object(Bucket=self.bucket, Key=self.key, Body=b"corrupt")["VersionId"]
        with self.assertRaises(BadRequestError):
            self.store.verify(self.key, version, self.manifest)

    def test_cleanup_removes_versions_and_markers_only_inside_exact_context_prefix(self):
        self.versioning()
        self.store.put(self.key, self.output, self.manifest)
        self.client.put_object(Bucket=self.bucket, Key=self.key, Body=b"another attempt")
        self.client.delete_object(Bucket=self.bucket, Key=self.key)
        other = "builds/context-two/keep"
        self.client.put_object(Bucket=self.bucket, Key=other, Body=b"keep")
        self.store.delete_prefix("builds/context-one/")
        listing = self.client.list_object_versions(Bucket=self.bucket)
        self.assertEqual([v["Key"] for v in listing.get("Versions", [])], [other])
        self.assertEqual(listing.get("DeleteMarkers", []), [])
