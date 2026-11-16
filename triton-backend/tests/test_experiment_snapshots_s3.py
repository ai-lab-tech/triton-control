"""Actual versioned S3 snapshots exclude unrelated models, versions and credential files."""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.config import Config

from app.schemas.lineage import InventoryV1
from app.schemas.serving_metadata import StartupModel
from app.services.candidates.artifacts import CaptureLimits, S3ArtifactStore
from app.services.snapshots.artifacts import SelectedModels


@unittest.skipUnless(os.getenv("CANDIDATE_IMPORT_TEST_S3_ENDPOINT"), "Set disposable MinIO endpoint")
class SnapshotS3Tests(unittest.TestCase):
    def test_selected_versions_and_retained_bytes_survive_source_changes(self):
        s3 = boto3.client(
            "s3",
            endpoint_url=os.environ["CANDIDATE_IMPORT_TEST_S3_ENDPOINT"],
            aws_access_key_id=os.getenv("CANDIDATE_IMPORT_TEST_S3_ACCESS_KEY", "candidate_test"),
            aws_secret_access_key=os.getenv("CANDIDATE_IMPORT_TEST_S3_SECRET_KEY", "candidate_test_password"),
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}, connect_timeout=3, read_timeout=10),
        )
        bucket = "snapshot-test-" + uuid4().hex
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})

        def cleanup():
            for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
                objects = [
                    {"Key": item["Key"], "VersionId": item["VersionId"]}
                    for kind in ("Versions", "DeleteMarkers")
                    for item in page.get(kind, [])
                ]
                if objects:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": objects})
            s3.delete_bucket(Bucket=bucket)
            s3.close()

        self.addCleanup(cleanup)
        for key in ("iris/config.pbtxt", "iris/1/model.onnx", "iris/2/model.onnx", "iris/.env", "other/1/model.onnx"):
            s3.put_object(Bucket=bucket, Key="models/" + key, Body=b"synthetic fixture bytes")
        source = SelectedModels(s3, bucket, "models", (StartupModel(name="iris", versions=("1",)),))
        store = S3ArtifactStore(s3, bucket, "snapshots")
        with tempfile.TemporaryDirectory() as directory:
            captured = source.capture(Path(directory), CaptureLimits())
            self.assertEqual(
                {file.path for file in captured.inventory.files}, {"iris/config.pbtxt", "iris/1/model.onnx"}
            )
            self.assertTrue(all(item["version_id"] for item in captured.receipt["objects"].values()))
            location = store.publish(
                Path(directory), captured.inventory, InventoryV1(files=()), {"kind": "test-fixture"}, CaptureLimits()
            )
            s3.put_object(Bucket=bucket, Key="models/iris/1/model.onnx", Body=b"new source revision")
            store.verify(location, captured.inventory, InventoryV1(files=()), CaptureLimits())
