"""Experiment capture invariants, confidentiality, durable recovery and access."""

import base64
import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import (
    CodeServerEntity,
    PreparationSnapshotEntity,
    PreparationTaskEntity,
    TritonInstanceEntity,
    UserEntity,
)
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.main import app
from app.repositories import operations as operation_repo
from app.schemas.experiment_snapshots import PreparationRequest, RuntimeObservation
from app.schemas.lineage import FileEntry, InventoryV1
from app.schemas.task_workspaces import SnapshotFile
from app.services.candidates.artifacts import CapturedSource, DirectoryArtifactStore
from app.services.operations import OperationWorker
from app.services.operations import service as operations
from app.services.snapshots import service, workspace_reader
from app.services.snapshots.inputs import download
from app.services.snapshots.runtime import actual_image
from app.services.snapshots.workspace import decode
from tests.test_preparation import PreparationFixture


def entry(path, data):
    return FileEntry(path=path, size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())


class Source:
    bucket = "fixture-models"
    prefix = "models/"

    def __init__(self):
        self.calls = 0
        self.changing = False
        self.empty = False
        self.data = b"synthetic model weights"

    def capture(self, destination, limits):
        self.calls += 1
        if self.empty:
            return CapturedSource(InventoryV1(files=()), {"kind": "test-fixture"})
        weights = self.data + (str(self.calls).encode() if self.changing else b"")
        inventory = []
        for path, data in (("iris/config.pbtxt", b'name: "iris"'), ("iris/1/model.onnx", weights)):
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inventory.append(entry(path, data))
        return CapturedSource(InventoryV1(files=tuple(inventory)), {"kind": "test-fixture"})


class SnapshotFixture(PreparationFixture):
    def setUp(self):
        super().setUp()
        clock = patch("app.repositories.operations.now", lambda _: datetime.utcnow())
        clock.start()
        self.addCleanup(clock.stop)
        self.setup_snapshots()

    def setup_snapshots(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="snapshot-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "retained"
        self.environment = patch.dict(
            os.environ,
            {
                "TRITON_INSTALLATION_ID": "test-development",
                "TRITON_CONTROL_MODE": "development",
                "TRITON_PREPARATION_SNAPSHOT_ROOT": str(self.root),
                "TRITON_PREPARATION_SNAPSHOT_PROFILE_ID": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.session.add(
            TritonInstanceEntity(
                id=1,
                name="experiment",
                url="http://serving.invalid",
                model_names=["iris"],
                is_self_deployed=True,
                deployment_runtime="kubernetes",
                deployment_namespace="test",
                deployment_name="experiment",
                s3_enabled=True,
                s3_bucket="fixture-models",
                s3_prefix="models",
            )
        )
        user = self.session.get(UserEntity, 4)
        user.assigned_instances = ["experiment"]
        self.session.add(user)
        self.session.add(
            CodeServerEntity(
                id=1,
                owner_user_id=4,
                name="project",
                namespace="test",
                statefulset_name="code",
                service_name="code",
                secret_name="private",
                image="fixture-workspace",
            )
        )
        self.session.commit()
        self.source = Source()
        self.runtime = RuntimeObservation(
            managed=True,
            deployment_uid="deployment-test",
            generation=1,
            observed_generation=1,
            resource_version="1",
            images=(
                {
                    "declared": "registry.invalid/fixture/model:latest",
                    "observed": "registry.invalid/fixture/model@sha256:" + hashlib.sha256(b"fixture-image").hexdigest(),
                    "node": "fixture",
                    "pod_uid": "pod-test",
                    "platform": "linux/arm64",
                },
            ),
            resources={"limits": {"cpu": "1"}},
        )
        self.adapter = service.SnapshotAdapter()
        patcher = patch.dict(operations.adapters, {service.KIND: self.adapter})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.original_source = service._source
        for name, value in (("_source", self.source), ("_observe", self.runtime)):
            mock = patch.object(service, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.worker = OperationWorker(operations, lambda: Session(self.engine))

    def request(self, **changes):
        return PreparationRequest.model_validate(
            {
                "title": "Prepare experiment",
                "models": [{"name": "iris", "versions": ["1"]}],
                "deployment_uid": "deployment-test",
                "expected_generation": 1,
                **changes,
            }
        )

    def submit(self, key="capture-1", request=None, claims=None):
        with Session(self.engine) as session:
            return service.submit(session, claims or self.submitter, 1, request or self.request(), key)

    def read_operation(self, operation_id):
        with Session(self.engine) as session:
            return operations.read(session, self.submitter, operation_id)

    def receipt(self, operation_id):
        with Session(self.engine) as session:
            return service.get(session, self.submitter, UUID(operation_id))

    def finish(self, **kwargs):
        accepted = self.submit(**kwargs)
        self.assertTrue(self.worker.run_once())
        self.assertEqual(self.read_operation(accepted.operation.id).state, "succeeded")
        return self.receipt(accepted.operation.id)


class SnapshotTests(SnapshotFixture, unittest.TestCase):
    def select_workspace(self, files: dict[str, bytes]):
        def capture(namespace, name, selected, destination):
            rows = [{"path": path, "data": base64.b64encode(files[path]).decode()} for path in selected]
            return decode(json.dumps({"files": rows}).encode(), selected, destination)

        self.enterContext(patch.object(service.WorkspaceProvider, "capture", side_effect=capture))
        return {"workspace_id": 1, "files": list(files)}

    def test_hf_capture_fingerprints_changed_bytes_at_same_declared_revision(self):
        # Arrange
        restored_manifest = Path(self.temporary.name) / "restored-serving.json"
        hints = json.dumps({
            "generator": "triton-hf-builder",
            "generator_version": "1.2.3",
            "runtime": "vllm",
            "gpu_count": 2,
            "max_new_tokens": True,
            "validation": {"static": "passed", "serving": "passed"},
        }).encode()
        manifest_path = "generated/serving.json"
        source = {
            "kind": "hf", "repository": "demo/model", "revision": "main",
            "serving_manifest": manifest_path, "checkpoint_files": ["iris/1/model.onnx"],
        }
        workspace = self.select_workspace({manifest_path: hints})

        # Act
        first = self.finish(request=self.request(source=source, workspace=workspace))
        self.source.data = b"new checkpoint at the same upstream revision"
        second = self.finish(key="recapture", request=self.request(
            source=source, workspace=workspace, previous_snapshot_id=first.id,
        ))
        original, revised = first.manifest.source_provenance, second.manifest.source_provenance
        assert original is not None and original.serving_manifest is not None and revised is not None
        download(self.session, SnapshotFile(snapshot_id=first.id, file=original.serving_manifest), restored_manifest)

        # Assert
        self.assertEqual(first.manifest.source_snapshot.kind, "hf")
        self.assertEqual(original.serving_manifest, entry(".workspace/" + manifest_path, hints))
        self.assertEqual((original.generator, original.generator_version), ("triton-hf-builder", "1.2.3"))
        self.assertEqual(original.generator_options, {"runtime": "vllm", "gpu_count": 2})
        self.assertEqual(restored_manifest.read_bytes(), hints)
        self.assertIn("hf_source_revision_missing", second.task.missing_inputs)
        self.assertIn("evaluation_inputs_missing", second.task.missing_inputs)
        self.assertIn("running_model_bytes_unverified", second.task.missing_inputs)
        for provenance in (original, revised):
            self.assertIsNotNone(provenance.checkpoint_inventory)
            self.assertIsNotNone(provenance.revision_fingerprint)
        self.assertNotEqual(original.checkpoint_inventory, revised.checkpoint_inventory)
        self.assertNotEqual(original.revision_fingerprint, revised.revision_fingerprint)
        self.assertNotEqual(first.manifest.source_snapshot.inventory, second.manifest.source_snapshot.inventory)
        self.assertNotEqual(first.task.id, second.task.id)
        self.assertEqual(second.manifest.previous_snapshot_id, first.id)
        self.assertEqual(first.manifest_digest, self.receipt(first.operation_id).manifest_digest)

    def test_example_capture_retains_documents_and_detects_an_omitted_repository_file(self):
        # Arrange
        model_card = "project/MODEL_CARD.md"
        license_file = "project/LICENSE"
        source_notes = "project/SOURCES.md"
        files = {
            model_card: b"Model card",
            license_file: b"License supplied by author",
            source_notes: b"Source notes",
            "project/iris/config.pbtxt": b'name: "iris"',
            "project/iris/2/model.onnx": b"Unselected version",
        }
        source = {
            "kind": "example", "repository": "github.com/example/models", "commit": "a" * 40,
            "project_path": "examples/iris", "workspace_model_root": "project",
            "model_card": model_card, "license_file": license_file, "source_documents": [source_notes],
        }
        workspace = self.select_workspace(files)

        # Act
        partial = self.finish(request=self.request(source=source, workspace=workspace))
        files["project/iris/1/model.onnx"] = self.source.data
        complete = self.finish(key="complete", request=self.request(
            source=source, workspace={**workspace, "files": list(files)},
        ))
        files["project/iris/config.pbtxt"] = b'name: "iris"\n# Uncommitted edit'
        changed = self.finish(key="changed", request=self.request(
            source=source, workspace={**workspace, "files": list(files)},
        ))

        # Assert
        provenance = partial.manifest.source_provenance
        assert provenance is not None and partial.manifest.request.source is not None
        self.assertEqual(partial.manifest.source_snapshot.kind, "git")
        self.assertEqual(partial.manifest.request.source.model_dump(mode="json"), source)
        self.assertEqual(provenance.documents, tuple(
            entry(".workspace/" + path, files[path]) for path in (model_card, license_file, source_notes)
        ))
        self.assertIn("workspace_model_comparison_incomplete", partial.task.missing_inputs)
        self.assertNotIn("workspace_model_bytes_differ", partial.task.missing_inputs)
        self.assertNotIn("workspace_model_comparison_incomplete", complete.task.missing_inputs)
        self.assertNotIn("workspace_model_bytes_differ", complete.task.missing_inputs)
        self.assertIn("workspace_model_bytes_differ", changed.task.missing_inputs)
        self.assertIn("running_model_bytes_unverified", complete.task.missing_inputs)
        self.assertNotEqual(complete.manifest.source_snapshot.inventory, changed.manifest.source_snapshot.inventory)
        self.assertNotIn("source_license_missing", partial.task.missing_inputs)

    def test_external_manual_capture_accepts_complete_workspace_model_and_attributes_author(self):
        # Arrange
        instance = self.session.get(TritonInstanceEntity, 1)
        instance.is_self_deployed = False
        self.session.add(instance)
        self.session.commit()
        workspace = self.select_workspace({
            "iris/config.pbtxt": b'name: "iris"',
            "iris/1/model.py": b"# Uncommitted model logic",
        })
        self.enterContext(patch.object(service, "_source", wraps=self.original_source))
        self.enterContext(patch.object(service, "_observe", return_value=RuntimeObservation(managed=False)))
        request = self.request(
            deployment_uid=None, expected_generation=None, workspace=workspace,
            source={"kind": "manual", "workspace_model_root": ".", "author": "Ada Example", "ai_assisted": True},
        )

        # Act
        receipt = self.finish(request=request)

        # Assert
        provenance = receipt.manifest.source_provenance
        assert provenance is not None
        self.assertEqual(receipt.manifest.source_snapshot.kind, "manual")
        self.assertEqual(receipt.manifest.request.source, request.source)
        self.assertEqual(provenance.submitter_user_id, receipt.manifest.submitter_user_id)
        self.assertEqual(receipt.manifest.inventory.files, ())
        self.assertIn(entry(".workspace/iris/1/model.py", b"# Uncommitted model logic"),
                      receipt.manifest.workspace_inventory.files)
        self.assertNotIn("model_artifacts_missing", receipt.task.missing_inputs)
        self.assertIn("external_runtime_unobserved", receipt.task.missing_inputs)
        self.assertIn("source_model_card_missing", receipt.task.missing_inputs)
        self.assertIn("source_license_missing", receipt.task.missing_inputs)

    def test_hf_capture_retains_unreadable_manifest_and_flags_unavailable_checkpoint(self):
        # Arrange
        path, data = "generated/serving.json", b'{"truncated":'
        workspace = self.select_workspace({path: data})
        request = self.request(workspace=workspace, source={
            "kind": "hf", "repository": "demo/model", "resolved_commit": "b" * 40,
            "serving_manifest": path, "checkpoint_files": ["iris/1/missing.bin"],
        })

        # Act
        receipt = self.finish(request=request)

        # Assert
        provenance = receipt.manifest.source_provenance
        assert provenance is not None
        self.assertEqual(provenance.serving_manifest, entry(".workspace/" + path, data))
        self.assertIsNone(provenance.checkpoint_inventory)
        self.assertIsNone(provenance.revision_fingerprint)
        self.assertIn("hf_generator_metadata_missing", receipt.task.missing_inputs)
        self.assertIn("hf_checkpoint_fingerprint_missing", receipt.task.missing_inputs)
        self.assertNotIn("hf_source_revision_missing", receipt.task.missing_inputs)

    def test_builder_acquisition_options_survive_capture(self):
        # Arrange
        manifest = {"generator": "triton-hf-builder", "generator_version": "1.2.3", "device": "cpu"}
        record = {"generator_version": "1.2.3", "runtime": "transformers", "device": "cpu", "max_new_tokens": 16}
        workspace = self.select_workspace({
            "model_repository/serving.json": json.dumps(manifest).encode(),
            "demo-source.json": json.dumps(record).encode(),
        })
        source = {"kind": "hf", "serving_manifest": "model_repository/serving.json",
                  "acquisition_record": "demo-source.json"}

        # Act
        receipt = self.finish(request=self.request(source=source, workspace=workspace))

        # Assert
        provenance = receipt.manifest.source_provenance
        assert provenance is not None
        self.assertEqual(
            provenance.generator_options, {"runtime": "transformers", "device": "cpu", "max_new_tokens": 16},
        )
        self.assertNotIn("hf_generator_metadata_conflict", receipt.task.missing_inputs)

    def test_manual_ai_attribution_round_trips_all_three_states(self):
        # Arrange
        workspace = self.select_workspace({"model.py": b"# handwritten or generated"})
        for value in (None, False, True):
            with self.subTest(value=value):
                source: dict[str, str | bool] = {"kind": "manual"}
                if value is not None:
                    source["ai_assisted"] = value

                # Act
                receipt = self.finish(
                    key=f"attribution-{value}", request=self.request(source=source, workspace=workspace),
                )

                # Assert
                self.assertIs(receipt.manifest.model_dump(mode="json")["request"]["source"]["ai_assisted"], value)

    def test_declared_source_files_must_be_selected(self):
        # Arrange
        workspace = {"workspace_id": 1, "files": ["project/notes.txt"]}
        invalid_sources = (
            {"kind": "example", "license_file": "project/LICENSE"},
            {"kind": "hf", "serving_manifest": "project/serving.json"},
            {"kind": "hf", "serving_manifest": "project/notes.txt"},
            {"kind": "hf", "acquisition_record": "demo-source.json"},
            {"kind": "manual", "workspace_model_root": "other-project"},
        )

        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                # Act / Assert
                self.request(source=source, workspace=workspace)

    def test_capture_commits_one_task_and_immutable_receipt_with_real_bytes(self):
        before = self.session.get(TritonInstanceEntity, 1).model_dump()
        receipt = self.finish()
        serialized = receipt.manifest.model_dump(mode="json")
        self.assertNotIn("source", serialized["request"])
        self.assertNotIn("source_provenance", serialized)
        self.assertEqual(receipt.task.source_snapshot_id, receipt.id)
        self.assertEqual(receipt.manifest.configuration["models"], ["iris"])
        self.assertIn("runtime_state_unverified", receipt.manifest.gaps)
        self.assertIn("running_model_bytes_unverified", receipt.task.missing_inputs)
        self.assertEqual(receipt.manifest.runtime.images[0].observed, self.runtime.images[0].observed)
        self.assertEqual(self.session.get(TritonInstanceEntity, 1).model_dump(), before)
        DirectoryArtifactStore(self.root).verify(
            receipt.artifacts, receipt.manifest.inventory, receipt.manifest.workspace_inventory, service.CaptureLimits()
        )
        with self.assertRaises(DBAPIError):
            row = self.session.get(PreparationSnapshotEntity, receipt.id)
            row.manifest_digest = "altered"
            self.session.add(row)
            self.session.commit()
        self.session.rollback()

    def test_original_configuration_remains_readable_without_credential_values(self):
        instance = self.session.get(TritonInstanceEntity, 1)
        instance.s3_endpoint = "https://legacy-user:synthetic-private-value@storage.invalid"
        self.session.add(instance)
        self.session.commit()
        # Use explicitly supplied model inputs, independently of legacy instance settings.
        receipt = self.finish(request=self.request(repository={"profile_id": 1, "prefix": "provided"}))
        self.assertEqual(receipt.manifest.configuration["models"], ["iris"])
        self.assertIsNone(receipt.manifest.configuration["s3_endpoint"])
        self.assertNotIn("synthetic-private-value", receipt.model_dump_json())
        instance.model_names = ["new-experiment"]
        self.session.add(instance)
        self.session.commit()
        self.assertEqual(self.receipt(receipt.operation_id).manifest.configuration["models"], ["iris"])

    def test_idempotent_replay_does_not_capture_again_or_create_another_task(self):
        first = self.finish()
        with patch.object(service, "_source", side_effect=AssertionError("must reuse operation")):
            replay = self.submit()
        self.assertEqual(replay.operation.id, first.operation_id)
        self.assertFalse(self.worker.run_once())
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)
        with self.assertRaises(ConflictError):
            self.submit(request=self.request(title="Different intent"))

    def test_explicit_recapture_links_revision_without_mutating_previous(self):
        first = self.finish()
        self.source.data = b"new synthetic weights"
        second = self.finish(key="capture-2", request=self.request(previous_snapshot_id=first.id))
        self.assertEqual(second.manifest.previous_snapshot_id, first.id)
        self.assertNotEqual(first.manifest.source_snapshot.inventory, second.manifest.source_snapshot.inventory)
        self.assertEqual(self.receipt(first.operation_id).manifest_digest, first.manifest_digest)

    def test_configuration_generation_changes_fail_without_task_and_retry_reuses_operation(self):
        accepted = self.submit()
        with patch.object(
            service, "_observe", side_effect=[self.runtime, self.runtime.model_copy(update={"generation": 2})]
        ):
            self.worker.run_once()
        self.assertEqual(self.read_operation(accepted.operation.id).state, "failed")
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])
        with Session(self.engine) as session:
            operations.retry(session, self.submitter, accepted.operation.id)
        self.worker.run_once()
        self.assertEqual(self.receipt(accepted.operation.id).operation_id, accepted.operation.id)

    def test_changed_inventory_never_publishes_mixed_bytes(self):
        self.source.changing = True
        accepted = self.submit()
        self.worker.run_once()
        self.assertEqual(self.read_operation(accepted.operation.id).state, "failed")
        self.assertEqual(self.session.exec(select(PreparationSnapshotEntity)).all(), [])

    def test_missing_artifacts_and_unknown_runtime_are_explicit_gaps(self):
        self.source.empty = True
        with patch.object(service, "_observe", return_value=self.runtime.model_copy(update={"images": ()})):
            receipt = self.finish(request=self.request(runtime_changes="requires_rebuild"))
        self.assertTrue(
            {"running_image_identity_unknown", "model_artifacts_missing", "runtime_changes_require_build"}
            <= set(receipt.manifest.gaps)
        )
        self.assertTrue(receipt.task.blocked_reasons)

    def test_external_endpoint_requires_explicit_inputs_and_does_not_invent_runtime(self):
        instance = self.session.get(TritonInstanceEntity, 1)
        instance.is_self_deployed = False
        instance.deployment_runtime = "external"
        self.session.add(instance)
        self.session.commit()
        with self.assertRaises(BadRequestError):
            self.submit()
        with patch.object(service, "_observe", return_value=RuntimeObservation(managed=False)):
            receipt = self.finish(request=self.request(repository={"profile_id": 1, "prefix": "provided"}))
        self.assertFalse(receipt.manifest.runtime.managed)
        self.assertIn("external_runtime_unobserved", receipt.manifest.gaps)

    def test_cancel_before_capture_creates_no_task_or_snapshot(self):
        accepted = self.submit()
        with Session(self.engine) as session:
            operations.cancel(session, self.submitter, accepted.operation.id)
        self.worker.run_once()
        self.assertEqual(self.read_operation(accepted.operation.id).state, "cancelled")
        self.assertEqual(self.source.calls, 0)
        self.assertEqual(self.session.exec(select(PreparationSnapshotEntity)).all(), [])

    def test_revoked_access_and_stale_admin_claims_do_not_capture(self):
        accepted = self.submit()
        actor = self.session.get(UserEntity, 4)
        actor.assigned_instances = []
        self.session.add(actor)
        self.session.commit()
        self.worker.run_once()
        with self.assertRaises(ForbiddenError):
            self.submit(key="other", claims={**self.submitter, "role": "admin"})
        self.assertEqual(self.source.calls, 0)
        self.assertEqual(self.session.exec(select(PreparationSnapshotEntity)).all(), [])
        self.assertIn("authorization", operation_repo.get(self.session, accepted.operation.id).message)

    def test_foreign_workspace_and_delegated_tokens_are_rejected(self):
        workspace = self.session.get(CodeServerEntity, 1)
        workspace.owner_user_id = 2
        self.session.add(workspace)
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            self.submit(request=self.request(workspace={"workspace_id": 1, "files": ["project/model.py"]}))
        with self.assertRaises(ForbiddenError):
            self.submit(claims={**self.submitter, "token_use": "workspace_deployment"})

    def test_production_mode_is_rejected(self):
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}), self.assertRaises(ForbiddenError):
            self.submit()

    def test_origin_deletion_does_not_erase_retained_snapshot(self):
        receipt = self.finish()
        self.session.delete(self.session.get(TritonInstanceEntity, 1))
        self.session.commit()
        self.assertEqual(self.receipt(receipt.operation_id).manifest_digest, receipt.manifest_digest)
        self.assertEqual(self.read_operation(receipt.operation_id).state, "succeeded")

    def test_workspace_files_change_identity_and_are_not_claimed_as_tested(self):
        data = b"# uncommitted project input"

        def capture(namespace, name, selected, destination):
            path = ".workspace/project/model.py"
            (destination / path).parent.mkdir(parents=True, exist_ok=True)
            (destination / path).write_bytes(data)
            return InventoryV1(files=(entry(path, data),))

        with patch("app.services.snapshots.service.WorkspaceProvider.capture", side_effect=capture):
            receipt = self.finish(request=self.request(workspace={"workspace_id": 1, "files": ["project/model.py"]}))
        self.assertEqual(receipt.manifest.workspace_relation, "selected_unverified_changes")
        self.assertIn("workspace_changes_not_tested", receipt.manifest.gaps)
        self.assertTrue(receipt.manifest.workspace_inventory.files)

    def test_secret_file_contents_fail_without_publication(self):
        self.source.data = b"-----BEGIN " + b"PRIVATE KEY----- synthetic"
        accepted = self.submit()
        self.worker.run_once()
        result = self.read_operation(accepted.operation.id)
        self.assertEqual(result.state, "failed")
        self.assertNotIn("PRIVATE KEY", result.message)
        self.assertEqual(self.session.exec(select(PreparationSnapshotEntity)).all(), [])

    def test_api_accepts_and_returns_durable_operation_location(self):
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.submitter
        self.addCleanup(app.dependency_overrides.clear)
        client = TestClient(app)
        self.addCleanup(client.close)
        result = client.post(
            "/api/instances/1/preparation-requests",
            json=self.request().model_dump(mode="json"),
            headers={"Idempotency-Key": "api-request"},
        )
        self.assertEqual(result.status_code, 202, result.text)
        self.assertEqual(result.headers["Location"], result.json()["operation"]["status_url"])


class WorkspaceReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "project").mkdir()
        (self.root / "project/model.py").write_text("uncommitted")

    def test_selected_uncommitted_file_excludes_unrelated_files(self):
        # Arrange
        (self.root / "unrelated.txt").write_text("not selected")

        # Act
        rows = workspace_reader.read(str(self.root), ("project/model.py",), 100, 100)

        # Assert
        self.assertEqual([row["path"] for row in rows], ["project/model.py"])
        self.assertEqual(base64.b64decode(rows[0]["data"]), b"uncommitted")

    def test_symlink_is_rejected(self):
        # Arrange
        (self.root / "project/link").symlink_to(self.root / "project/model.py")

        # Act / Assert
        with self.assertRaises(OSError):
            workspace_reader.read(str(self.root), ("project/link",), 100, 100)

    def test_oversized_file_is_rejected(self):
        # Arrange
        maximum = 1

        # Act / Assert
        with self.assertRaises(ValueError):
            workspace_reader.read(str(self.root), ("project/model.py",), maximum, maximum)

    def test_file_mutation_during_read_is_rejected(self):
        # Arrange
        original_fstat = os.fstat
        calls = 0

        def mutate_before_second_stat(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / "project/model.py").write_text("changed during capture")
            return original_fstat(fd)

        self.enterContext(patch.object(workspace_reader.os, "fstat", side_effect=mutate_before_second_stat))

        # Act
        with self.assertRaises(ValueError) as error:
            workspace_reader.read(str(self.root), ("project/model.py",), 100, 100)

        # Assert
        self.assertEqual(str(error.exception), "changed")

    def test_remote_response_selection_and_secret_paths_are_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory)
            for path in ("../escape", ".env", ".aws/credentials", "other.py"):
                data = json.dumps({"files": [{"path": path, "data": "eA=="}]}).encode()
                with self.subTest(path=path), self.assertRaises(BadRequestError):
                    decode(data, ("project/model.py",), destination)

    def test_bare_containerd_id_is_not_a_manifest_digest(self):
        self.assertIsNone(actual_image("containerd://sha256:" + "a" * 64))
        self.assertIsNone(actual_image("registry.invalid/model:latest"))
