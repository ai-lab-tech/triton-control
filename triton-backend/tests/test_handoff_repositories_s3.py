"""Opt-in real S3 roundtrip: capture a folder and restore pinned bytes after replacement."""

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

import boto3

from app.core.crypto import encrypt_secret
from app.db.entities import S3ProfileEntity
from app.services import handoff_sources, handoffs, task_workspace_access, task_workspaces
from app.services.development.task_stage_runner import stage
from tests.test_handoffs import HandoffFixture

ENDPOINT = os.getenv("REPOSITORY_TEST_S3_ENDPOINT", "")


@unittest.skipUnless(ENDPOINT, "Set REPOSITORY_TEST_S3_ENDPOINT and AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY")
class RepositoryS3Tests(HandoffFixture, unittest.TestCase):
    def test_repository_snapshot_restores_original_versions_after_source_changes(self):
        # Arrange: an isolated versioned bucket, real provider I/O and existing task stager.
        client = boto3.client("s3", endpoint_url=ENDPOINT, region_name="us-east-1")
        self.addCleanup(client.close)
        bucket = "repository-test-" + uuid4().hex
        client.create_bucket(Bucket=bucket)

        def cleanup():
            for page in client.get_paginator("list_object_versions").paginate(Bucket=bucket):
                for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                    client.delete_object(Bucket=bucket, Key=item["Key"], VersionId=item["VersionId"])
            client.delete_bucket(Bucket=bucket)

        self.addCleanup(cleanup)
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        contents = {"repo/iris/config.pbtxt": b'name: "iris"', "repo/iris/1/model.onnx": b"original model",
                    "repo/iris/1/__init__.py": b"", "data.csv": b"data", "baseline.json": b"{}"}
        for key, data in contents.items():
            client.put_object(Bucket=bucket, Key="allowed/" + key, Body=data)
        profile = self.session.get(S3ProfileEntity, 1)
        profile.endpoint, profile.bucket = ENDPOINT, bucket
        profile.access_key = os.environ["AWS_ACCESS_KEY_ID"]
        profile.secret_key_enc = encrypt_secret(os.environ["AWS_SECRET_ACCESS_KEY"])
        profile.force_path_style = True
        self.session.add(profile)
        self.session.commit()
        request = self.request(
            source={"kind": "s3_repository", "profile_id": 1, "path": "repo/"},
            dataset={"profile_id": 1, "path": "data.csv"}, baseline={"profile_id": 1, "path": "baseline.json"},
            expected_behavior="predict", acceptance_criteria="matches baseline",
        )
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))

        # Act
        receipt = handoffs.submit(self.session, self.submitter, request)
        client.put_object(Bucket=bucket, Key="allowed/repo/iris/1/model.onnx", Body=b"replacement")
        repeated = handoffs.submit(self.session, self.submitter, request)
        inputs = task_workspace_access.load_inputs(self.session, receipt.task.id)
        document = task_workspaces.document(receipt.task.id, inputs)

        def fetch(index):
            destination = root / f"download-{index}"
            handoff_sources.download(self.session, inputs.files[index].artifact, destination)
            return destination.open("rb")

        stage(root, document, fetch)

        # Assert
        self.assertEqual(receipt.id, repeated.id)
        self.assertEqual((root / "verified/source/iris/1/model.onnx").read_bytes(), b"original model")
        self.assertEqual((root / "verified/source/iris/1/__init__.py").read_bytes(), b"")
        self.assertEqual((root / "verified/source/iris/config.pbtxt").read_bytes(), b'name: "iris"')
        self.assertTrue(all(item["version_id"] != "null" for item in document["files"]))
