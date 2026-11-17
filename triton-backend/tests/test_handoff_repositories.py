"""Repository capture, immutable retries and task staging, using Arrange/Act/Assert."""

import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import select

from app.api.handoffs_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import HandoffEntity, PreparationTaskEntity, S3ProfileEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.schemas.handoffs import S3RepositoryInput
from app.schemas.preparation_settings import PreparationLimits
from app.services import handoff_repositories as repositories
from app.services import handoff_sources, handoffs, task_workspace_access, task_workspaces
from app.services.development.task_stage_runner import stage
from tests.test_handoffs import HandoffFixture


class RepositoryTests(HandoffFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.configure_storage()

    def configure_storage(self):
        self.contents = {
            "repo/iris/config.pbtxt": b'name: "iris"',
            "repo/iris/1/model.onnx": b"model bytes",
            "repo/iris/1/helpers/__init__.py": b"",
            "repo/serving.json": b'{"image":"registry/serving"}',
            "repo-other/private": b"outside prefix",
            "data.csv": b"data", "baseline.json": b"{}",
        }
        self.client = MagicMock()
        self.client.get_paginator.return_value.paginate.side_effect = self.pages
        self.client.head_object.side_effect = self.head
        self.client.get_object.side_effect = self.get_object
        self.enterContext(patch.object(repositories, "build_s3_client", return_value=self.client))
        self.enterContext(patch.object(handoff_sources, "build_s3_client", return_value=self.client))

    def pages(self, **kwargs):
        entries = [{"Key": "allowed/" + path, "Size": len(data)}
                   for path, data in self.contents.items() if ("allowed/" + path).startswith(kwargs["Prefix"])]
        # Multiple pages exercise full traversal. Directory markers are ignored.
        return [{"Contents": entries[:2]}, {"Contents": entries[2:] + [
            {"Key": kwargs["Prefix"] + "iris/", "Size": 0},
        ]}]

    def head(self, **kwargs):
        data = self.contents[kwargs["Key"].removeprefix("allowed/")]
        return {"ContentLength": len(data), "VersionId": "v1"}

    def get_object(self, **kwargs):
        data = self.contents[kwargs["Key"].removeprefix("allowed/")]
        return {"VersionId": "v1", "Body": io.BytesIO(data)}

    def repository_request(self, **changes):
        return self.request(
            source={"kind": "s3_repository", "profile_id": 1, "path": "repo/"},
            dataset={"profile_id": 1, "path": "data.csv"},
            baseline={"profile_id": 1, "path": "baseline.json"},
            expected_behavior="correct prediction", acceptance_criteria="matches baseline", **changes,
        )

    def test_pins_every_file_and_preserves_relative_layout_including_empty_files(self):
        # Arrange
        source = self.repository_request().source
        # Act
        captured = repositories.resolve(self.session, source)
        # Assert
        self.assertEqual([file.file.path for file in captured.files], [
            "iris/1/helpers/__init__.py", "iris/1/model.onnx", "iris/config.pbtxt", "serving.json",
        ])
        for file in captured.files:
            data = self.contents[file.source.path]
            self.assertEqual(file.file.digest, "sha256:" + hashlib.sha256(data).hexdigest())
            self.assertEqual(file.version_id, "v1")
            self.assertEqual(file.file.size, len(data))
        self.assertTrue(all(call.kwargs["VersionId"] == "v1" for call in self.client.get_object.call_args_list))
        self.assertTrue(all(call.kwargs["Prefix"] == "allowed/repo/"
                            for call in self.client.get_paginator.return_value.paginate.call_args_list))

    def test_retry_keeps_snapshot_after_live_folder_changes(self):
        # Arrange
        request = self.repository_request()
        first = handoffs.submit(self.session, self.submitter, request)
        self.contents["repo/iris/1/model.onnx"] = b"changed"
        capture = self.enterContext(patch.object(repositories, "resolve", side_effect=AssertionError("must reuse")))
        # Act
        repeated = handoffs.submit(self.session, self.submitter, request)
        # Assert
        self.assertEqual(repeated.id, first.id)
        self.assertEqual(repeated.source_snapshot.inventory, first.source_snapshot.inventory)
        capture.assert_not_called()
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)

    def test_new_revision_captures_new_inventory(self):
        # Arrange
        first = handoffs.submit(self.session, self.submitter, self.repository_request())
        self.contents["repo/iris/1/model.onnx"] = b"changed model"
        # Act
        second = handoffs.submit(self.session, self.submitter, self.repository_request(revision="2"))
        # Assert
        self.assertNotEqual(first.task.id, second.task.id)
        self.assertNotEqual(first.source_snapshot.inventory, second.source_snapshot.inventory)

    def test_workspace_restores_repository_without_archive_extraction(self):
        # Arrange
        receipt = handoffs.submit(self.session, self.submitter, self.repository_request())
        inputs = task_workspace_access.load_inputs(self.session, receipt.task.id)
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        document = task_workspaces.document(receipt.task.id, inputs)
        def fetch(index):
            return io.BytesIO(self.contents[inputs.files[index].artifact.source.path])
        # Act
        task_workspace_access.authorize_inputs(self.session, inputs)
        stage(root, document, fetch)
        # Assert
        self.assertEqual((root / "verified/source/iris/config.pbtxt").read_bytes(), b'name: "iris"')
        self.assertEqual((root / "verified/source/iris/1/model.onnx").read_bytes(), b"model bytes")
        self.assertEqual((root / "verified/source/iris/1/helpers/__init__.py").read_bytes(), b"")
        self.assertEqual(len(document["files"]), 6)
        self.assertFalse((root / "verified/source/iris/config.pbtxt").stat().st_mode & 0o222)

    def test_unversioned_file_rejects_entire_submission(self):
        # Arrange
        self.client.head_object.side_effect = lambda **kwargs: {"VersionId": "null", "ContentLength": 1}
        request = self.repository_request()
        # Act
        with self.assertRaises(BadRequestError) as error:
            handoffs.submit(self.session, self.submitter, request)
        # Assert
        self.assertIn("versioning", str(error.exception))
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])

    def test_changing_membership_during_capture_creates_no_task(self):
        # Arrange
        original = self.pages
        count = 0
        def changing(**kwargs):
            nonlocal count
            count += 1
            if count == 2:
                self.contents["repo/iris/2/model.onnx"] = b"new version"
            return original(**kwargs)
        self.client.get_paginator.return_value.paginate.side_effect = changing
        # Act
        with self.assertRaises(ConflictError) as error:
            handoffs.submit(self.session, self.submitter, self.repository_request())
        # Assert
        self.assertIn("changed during capture", str(error.exception))
        self.assertEqual(self.session.exec(select(HandoffEntity)).all(), [])

    def test_changed_object_version_during_capture_is_rejected(self):
        # Arrange
        original = self.head
        calls = 0
        def changing(**kwargs):
            nonlocal calls
            calls += 1
            return {**original(**kwargs), "VersionId": "v2" if calls > 8 else "v1"}
        self.client.head_object.side_effect = changing
        # Act
        with self.assertRaises(ConflictError) as error:
            repositories.resolve(self.session, self.repository_request().source)
        # Assert
        self.assertIn("changed during capture", str(error.exception))

    def test_invalid_paths_are_rejected_before_download(self):
        for path in ("../outside", "iris/../escape", "iris//bad", "iris/1/back\\slash", "iris/1/a:b"):
            with self.subTest(path=path):
                # Arrange
                self.contents["repo/" + path] = b"invalid"
                self.client.get_object.reset_mock()
                # Act
                with self.assertRaises(BadRequestError) as error:
                    repositories.resolve(self.session, self.repository_request().source)
                # Assert
                self.assertIn("unsafe", str(error.exception))
                self.client.get_object.assert_not_called()
                del self.contents["repo/" + path]

    def test_file_directory_collision_is_rejected(self):
        # Arrange
        self.contents["repo/iris/1"] = b"file blocking directory"
        # Act
        with self.assertRaises(BadRequestError) as error:
            repositories.resolve(self.session, self.repository_request().source)
        # Assert
        self.assertIn("collision", str(error.exception))

    def test_empty_or_non_repository_prefix_is_rejected(self):
        # Arrange
        source = S3RepositoryInput(kind="s3_repository", profile_id=1, path="missing")
        # Act
        with self.assertRaises(BadRequestError) as error:
            repositories.resolve(self.session, source)
        # Assert
        self.assertIn("config.pbtxt", str(error.exception))

    def test_repository_limits_reject_before_downloading(self):
        for limit in ("MAX_BYTES", "MAX_FILES"):
            with self.subTest(limit=limit):
                # Arrange
                self.client.get_object.reset_mock()
                # Act
                guard = (patch.object(repositories, "MAX_FILES", 1) if limit == "MAX_FILES" else
                         patch.object(repositories, "effective", return_value=PreparationLimits().model_copy(
                             update={"max_repository_gib": 0})))
                with guard, self.assertRaises(BadRequestError):
                    repositories.resolve(self.session, self.repository_request().source)
                # Assert
                self.client.get_object.assert_not_called()

    def test_provider_denial_is_sanitized(self):
        # Arrange
        self.client.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "secret"}}, "HeadObject",
        )
        # Act
        with self.assertRaises(ForbiddenError) as error:
            repositories.resolve(self.session, self.repository_request().source)
        # Assert
        self.assertNotIn("secret", str(error.exception))

    def test_expired_capture_stops_before_listing(self):
        # Arrange
        clock = self.enterContext(patch.object(repositories.time, "monotonic", side_effect=[0, 3601]))
        # Act
        with self.assertRaises(BadRequestError) as error:
            repositories.resolve(self.session, self.repository_request().source)
        # Assert
        self.assertIn("time limit", str(error.exception))
        self.client.get_object.assert_not_called()
        self.assertEqual(clock.call_count, 2)

    def test_source_profile_changes_revoke_workspace_access(self):
        # Arrange
        receipt = handoffs.submit(self.session, self.submitter, self.repository_request())
        inputs = task_workspace_access.load_inputs(self.session, receipt.task.id)
        profile = self.session.get(S3ProfileEntity, 1)
        profile.prefix = "elsewhere"
        self.session.add(profile)
        self.session.commit()
        # Act
        with self.assertRaises(ForbiddenError) as error:
            task_workspace_access.authorize_inputs(self.session, inputs)
        # Assert
        self.assertIn("authorized profile", str(error.exception))

    def test_repository_selector_cannot_claim_a_single_object_version(self):
        # Arrange
        payload = {"kind": "s3_repository", "profile_id": 1, "path": "repo", "version_id": "v1"}
        # Act
        with self.assertRaises(ValidationError) as error:
            S3RepositoryInput.model_validate(payload)
        # Assert
        self.assertIn("extra_forbidden", str(error.exception))

    def test_oversized_inventory_is_rejected_without_creating_a_task(self):
        # Arrange
        self.enterContext(patch.object(repositories, "MAX_MANIFEST_BYTES", 100))
        request = self.repository_request()
        # Act
        with self.assertRaises(BadRequestError) as error:
            handoffs.submit(self.session, self.submitter, request)
        # Assert
        self.assertIn("metadata limit", str(error.exception))
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])

    def test_file_and_folder_at_same_key_remain_distinct_submissions(self):
        # Arrange
        self.contents["repo"] = b"independent object at prefix key"
        first = handoffs.submit(self.session, self.submitter, self.request(source={"profile_id": 1, "path": "repo"}))
        # Act
        folder = handoffs.submit(self.session, self.submitter, self.repository_request())
        repeated = handoffs.submit(self.session, self.submitter, self.repository_request())
        # Assert
        self.assertNotEqual(first.id, folder.id)
        self.assertEqual(folder.id, repeated.id)
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 2)

    def test_http_roundtrip_exposes_each_file_and_exact_snapshot(self):
        # Arrange
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.submitter
        client = self.enterContext(TestClient(app))
        request = self.repository_request().model_dump(mode="json")
        # Act
        submitted = client.post("/api/preparation/handoffs", json=request)
        payload = submitted.json()
        fetched = client.get("/api/preparation/handoffs/" + payload["id"])
        # Assert
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["source"], payload["source"])
        self.assertEqual(payload["request"]["source"]["kind"], "s3_repository")
        self.assertEqual(len(payload["source"]["files"]), 4)
        self.assertTrue(payload["source_snapshot"]["inventory"].startswith("sha256:"))

    def test_workspace_download_rejects_tampered_version_or_bytes(self):
        for changed in ("version", "bytes"):
            with self.subTest(changed=changed):
                # Arrange
                artifact = repositories.resolve(self.session, self.repository_request().source).files[1]
                root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                response = {"VersionId": "wrong" if changed == "version" else artifact.version_id,
                            "Body": io.BytesIO(b"model bytes" if changed == "version" else b"wrong bytes")}
                original = self.client.get_object.side_effect
                self.client.get_object.side_effect = lambda **kwargs: response
                # Act
                with self.assertRaises(BadRequestError) as error:
                    handoff_sources.download(self.session, artifact, root / "model")
                # Assert
                self.assertIn("snapshot", str(error.exception))
                self.assertTrue(response["Body"].closed)
                self.client.get_object.side_effect = original
