"""Opt-in real MinIO source capture; uses an isolated disposable bucket."""

import hashlib
import os
import unittest
from uuid import uuid4

import boto3

from app.exceptions import ForbiddenError
from app.schemas.handoffs import S3Input
from app.services import handoff_sources
from tests.test_handoffs import HandoffFixture

ENDPOINT = os.getenv("HANDOFF_TEST_S3_ENDPOINT", "")


@unittest.skipUnless(ENDPOINT, "Set HANDOFF_TEST_S3_ENDPOINT for real S3 integration")
class HandoffS3Tests(HandoffFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            aws_access_key_id="handoff_test",
            aws_secret_access_key="handoff_test_password",
            region_name="us-east-1",
        )
        self.bucket = "handoff-test-" + uuid4().hex
        self.client.create_bucket(Bucket=self.bucket)
        self.client.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
        self.addCleanup(self.cleanup_bucket)
        from app.db.entities import S3ProfileEntity

        profile = self.session.get(S3ProfileEntity, 1)
        profile.endpoint, profile.bucket = ENDPOINT, self.bucket
        profile.access_key, profile.secret_key_enc = "handoff_test", "handoff_test_password"
        self.session.add(profile)
        self.session.commit()

    def cleanup_bucket(self):
        response = self.client.list_object_versions(Bucket=self.bucket)
        objects = [
            {"Key": row["Key"], "VersionId": row["VersionId"]}
            for row in response.get("Versions", []) + response.get("DeleteMarkers", [])
        ]
        if objects:
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
        self.client.delete_bucket(Bucket=self.bucket)
        self.client.close()

    def test_versioned_capture_survives_latest_object_replacement(self):
        version = self.client.put_object(Bucket=self.bucket, Key="allowed/model.onnx", Body=b"original")["VersionId"]
        first = handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx"))
        self.client.put_object(Bucket=self.bucket, Key="allowed/model.onnx", Body=b"replacement")
        pinned = handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx", version_id=version))
        latest = handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx"))
        self.assertEqual(first.file.digest, "sha256:" + hashlib.sha256(b"original").hexdigest())
        self.assertEqual(first.file.digest, pinned.file.digest)
        self.assertNotEqual(first.file.digest, latest.file.digest)

    def test_missing_object_fails_authorization(self):
        with self.assertRaises(ForbiddenError):
            handoff_sources.resolve(self.session, S3Input(profile_id=1, path="unavailable.onnx"))
