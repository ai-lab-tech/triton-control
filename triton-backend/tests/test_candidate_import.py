"""Neutral import behavior, security, immutable storage and contract fixtures."""

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.db.entities import CandidateImportEntity, ServingCandidateEntity, UserEntity
from app.db.schema import create_tables
from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    ServiceUnavailableError,
    UnprocessableEntityError,
)
from app.schemas.candidate_import import PreparedImportRequest
from app.schemas.lineage import EngineeringAttempt, InventoryV1, Origin, Snapshot
from app.schemas.preparation import ReasonRequest, TaskCreate, TaskRevision
from app.services import preparation, teams
from app.services.candidates.artifacts import CaptureLimits, DirectoryArtifactStore, DirectorySource
from app.services.candidates.importer import get_import, import_repository
from app.services.lineage import inventory_digest
from app.services.lineage_inventory import measure

CONFIG = b'name: "iris"\nbackend: "onnxruntime"\nmax_batch_size: 0\n'


def write_repository(root, config=CONFIG, weights=b"structural-test-weight-bytes"):
    (root / "iris" / "1").mkdir(parents=True)
    (root / "iris" / "config.pbtxt").write_bytes(config)
    (root / "iris" / "1" / "model.onnx").write_bytes(weights)


def request(record="candidate-1", **values):
    return PreparedImportRequest(
        candidate_origin=Origin(installation_id="fixture-dev", record_id=record),
        capture_origin=Origin(installation_id="fixture-dev", record_id="capture-" + record),
        manual_source_origin=Origin(installation_id="fixture-manual", record_id="prepared-input"),
        triton_version="26.06",
        **values,
    )


class CandidateFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        write_repository(self.source)
        self.store = DirectoryArtifactStore(self.root / "artifacts")
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(self.engine, "connect")
        def setup_sqlite(connection, _):
            connection.isolation_level = None
            connection.execute("PRAGMA foreign_keys=ON")

        @event.listens_for(self.engine, "begin")
        def begin(connection):
            connection.exec_driver_sql("BEGIN")

        create_tables(self.engine)
        self.addCleanup(self.engine.dispose)
        self.session = Session(self.engine)
        self.addCleanup(self.session.close)
        self.seed()

    def seed(self):
        self.session.add_all(
            [
                UserEntity(id=i, name=f"User {i}", email=f"user{i}@test.invalid", role=role, password_hash="hash")
                for i, role in [(1, "admin"), (2, "member"), (3, "member"), (4, "viewer")]
            ]
        )
        self.session.commit()
        self.admin = {"email": "user1@test.invalid", "role": "admin"}
        self.engineer = {"email": "user2@test.invalid", "role": "member"}
        self.other = {"email": "user3@test.invalid", "role": "member"}
        self.group = teams.initialize_engineering_group(self.session, self.admin)
        teams.set_member(self.session, self.admin, self.group.id, 2, active=True)

    def do_import(self, req=None, claims=None, source=None, store=None, **options):
        return import_repository(
            self.session,
            claims or self.engineer,
            req or request(),
            source or DirectorySource(self.source),
            store or self.store,
            **options,
        )

    def counts(self):
        return (
            len(self.session.exec(select(ServingCandidateEntity)).all()),
            len(self.session.exec(select(CandidateImportEntity)).all()),
        )


class CandidateImportTests(CandidateFixture, unittest.TestCase):
    def test_manual_import_captures_exact_bytes_inventory_origin_and_missing_inputs(self):
        original = {
            p.relative_to(self.source).as_posix(): p.read_bytes() for p in self.source.rglob("*") if p.is_file()
        }
        result = self.do_import()
        self.assertEqual(result.candidate.plugin_references, ())
        self.assertEqual(result.candidate.derived_from_snapshot.origin, request().manual_source_origin)
        self.assertEqual(result.candidate.derived_from_snapshot.kind, "manual")
        self.assertEqual(result.completeness, "evaluation_inputs_missing")
        self.assertIn("evaluation_dataset", result.missing_evaluation_inputs)
        self.assertEqual(result.candidate.attempts[-1].operation, "packaging")
        self.assertEqual(result.models[0].backend, "onnxruntime")
        snapshot = self.store.root / result.artifacts.reference["snapshot_id"]
        for entry in result.inventory.files:
            self.assertEqual((snapshot / "repository" / entry.path).read_bytes(), original[entry.path])
            self.assertEqual(entry.digest, "sha256:" + hashlib.sha256(original[entry.path]).hexdigest())
            self.assertEqual((self.source / entry.path).read_bytes(), original[entry.path])
        self.assertEqual(self.counts(), (1, 1))
        self.assertEqual(get_import(self.session, self.engineer, result.candidate.origin, self.store), result)

    def test_identical_copy_is_deterministic_and_repeat_does_not_publish_again(self):
        first = self.do_import()
        copied = self.root / "copy"
        shutil.copytree(self.source, copied)
        second = self.do_import(source=DirectorySource(copied))
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.store.root.iterdir())), 1)
        another = self.do_import(request("candidate-2"), source=DirectorySource(copied))
        self.assertEqual(first.inventory, another.inventory)
        self.assertEqual(first.candidate.output_inventory, another.candidate.output_inventory)

    def test_changed_weights_require_new_identity_and_leave_original_snapshot_unchanged(self):
        first = self.do_import()
        (self.source / "iris/1/model.onnx").write_bytes(b"changed fixture weights")
        with self.assertRaises(ConflictError):
            self.do_import()
        changed = self.do_import(request("candidate-2"))
        self.assertNotEqual(changed.candidate.output_inventory, first.candidate.output_inventory)
        self.assertEqual(get_import(self.session, self.engineer, first.candidate.origin, self.store), first)

    def test_changed_config_alters_inventory_and_previous_candidate_is_separate_from_source(self):
        first = self.do_import()
        (self.source / "iris/config.pbtxt").write_bytes(CONFIG + b"instance_group [{ kind: KIND_CPU }]\n")
        second = self.do_import(request("candidate-2", previous_serving_candidate=first.candidate.origin))
        self.assertNotEqual(first.candidate.output_inventory, second.candidate.output_inventory)
        self.assertEqual(second.candidate.previous_serving_candidate, first.candidate.origin)
        self.assertEqual(second.candidate.derived_from_snapshot.origin, first.candidate.derived_from_snapshot.origin)

    def test_root_lineage_receipts_are_retained_outside_model_inventory(self):
        baseline = self.do_import()
        metadata = b'{"origin": "keep exact whitespace"}  \n'
        (self.source / "lineage.json").write_bytes(metadata)
        (self.source / ".triton-control").mkdir()
        (self.source / ".triton-control/receipt.json").write_bytes(b"original receipt")
        captured = self.do_import(request("candidate-2"))
        self.assertEqual(baseline.candidate.output_inventory, captured.candidate.output_inventory)
        self.assertEqual(len(captured.metadata_inventory.files), 2)
        path = self.store.root / captured.artifacts.reference["snapshot_id"] / "source-metadata/lineage.json"
        self.assertEqual(path.read_bytes(), metadata)
        manifest = self.store.root / captured.artifacts.reference["snapshot_id"] / "manifest.json"
        self.assertNotIn("manifest.json", {entry.path for entry in captured.inventory.files})
        self.assertEqual(json.loads(manifest.read_bytes())["candidate_digest"], captured.candidate_digest)

    def test_nested_lineage_filename_is_a_model_artifact(self):
        baseline = self.do_import()
        (self.source / "iris/1/lineage.json").write_bytes(b"runtime data")
        changed = self.do_import(request("candidate-2"))
        self.assertNotEqual(baseline.candidate.output_inventory, changed.candidate.output_inventory)
        self.assertEqual(changed.metadata_inventory.files, ())

    def test_actual_prior_attempts_and_cross_installation_origin_are_preserved(self):
        before = InventoryV1(files=())
        output = measure(self.source)
        source = Snapshot(
            origin=Origin(installation_id="external-training", record_id="exact-source"),
            kind="hf",
            inventory=inventory_digest(before),
        )
        actual = EngineeringAttempt(
            origin=Origin(installation_id="external-engineer", record_id="actual-conversion"),
            operation="conversion",
            input_inventory=inventory_digest(before),
            output_inventory=inventory_digest(output),
        )
        req = PreparedImportRequest(
            candidate_origin=Origin(installation_id="fixture-dev", record_id="candidate-1"),
            capture_origin=Origin(installation_id="fixture-dev", record_id="capture-1"),
            source_snapshot=source,
            attempts=(actual,),
            prior_inventories=(before,),
            triton_version="26.06",
        )
        result = self.do_import(req)
        self.assertEqual(result.candidate.derived_from_snapshot, source)
        self.assertEqual(result.candidate.attempts[0], actual)
        self.assertEqual(len(result.candidate.attempts), 2)

    def test_broken_attempt_chain_and_missing_prior_inventory_are_rejected(self):
        req = request().model_dump()
        req.update(
            manual_source_origin=None,
            source_snapshot=Snapshot(
                origin=Origin(installation_id="outside", record_id="source"),
                kind="manual",
                inventory="sha256:" + "a" * 64,
            ),
        )
        with self.assertRaises(BadRequestError):
            self.do_import(PreparedImportRequest(**req))
        self.assertEqual(self.counts(), (0, 0))

    def test_expected_backend_and_declared_model_filename_are_checked(self):
        with self.assertRaises(BadRequestError):
            self.do_import(request(expected_backends={"iris": "python"}))
        (self.source / "iris/config.pbtxt").write_bytes(CONFIG + b'default_model_filename: "missing.onnx"\n')
        with self.assertRaises(BadRequestError):
            self.do_import()
        self.assertEqual(self.counts(), (0, 0))

    def test_invalid_layouts_and_backend_conflicts_never_publish(self):
        for config in (
            b'name: "different"\nbackend: "onnxruntime"',
            b'name: "iris"',
            b'backend: "python" platform: "onnxruntime_onnx"',
            b'backend: "onnxruntime" default_model_filename: "../../outside"',
            b'backend: "ensemble" ensemble_scheduling { step { model_name: "external" model_version: -1 } }',
        ):
            (self.source / "iris/config.pbtxt").write_bytes(config)
            with self.subTest(config=config), self.assertRaises(BadRequestError):
                self.do_import()
        self.assertEqual(self.counts(), (0, 0))

    def test_malformed_proto_unknown_schema_and_unsafe_versions_are_rejected(self):
        (self.source / "iris/config.pbtxt").write_bytes(b'backend: "onnxruntime" unknown_field: true')
        with self.assertRaises(UnprocessableEntityError):
            self.do_import()
        (self.source / "iris/config.pbtxt").write_bytes(CONFIG)
        with self.assertRaises(UnprocessableEntityError):
            self.do_import(request().model_copy(update={"triton_version": "unknown"}))
        (self.source / "iris/1").rename(self.source / "iris/01")
        with self.assertRaises(BadRequestError):
            self.do_import()

    def test_missing_config_version_and_empty_input_are_rejected(self):
        (self.source / "iris/config.pbtxt").unlink()
        with self.assertRaises(BadRequestError):
            self.do_import()
        (self.source / "iris/config.pbtxt").write_bytes(CONFIG)
        (self.source / "iris/1/model.onnx").unlink()
        with self.assertRaises(BadRequestError):
            self.do_import()
        (self.source / "iris/config.pbtxt").unlink()
        with self.assertRaises(BadRequestError):
            self.do_import()

    def test_symlinks_hardlinks_and_special_files_cannot_escape_capture(self):
        outside = self.root / "outside"
        outside.write_bytes(b"must not be consumed")
        link = self.source / "iris/1/escape"
        for kind in ("symlink", "hardlink", "fifo"):
            if kind == "symlink":
                link.symlink_to(outside)
            elif kind == "hardlink":
                os.link(outside, link)
            else:
                os.mkfifo(link)
            with self.subTest(kind=kind), self.assertRaises(BadRequestError):
                self.do_import()
            link.unlink()
        self.assertEqual(self.counts(), (0, 0))

    def test_symlink_root_parent_and_traversal_are_rejected(self):
        link = self.root / "linked-parent"
        link.symlink_to(self.source, target_is_directory=True)
        for path in (link, link / "iris", self.source / ".." / "input"):
            with self.subTest(path=path), self.assertRaises(BadRequestError):
                self.do_import(source=DirectorySource(path))
        invalid = self.source / "iris/1" / "bad\\name"
        invalid.write_bytes(b"invalid path")
        with self.assertRaises(BadRequestError):
            self.do_import()

    def test_capture_detects_mutation_and_cleans_staging_without_publication(self):
        from app.services.candidates import artifacts

        original = artifacts._Budget.copy

        def mutate(budget, stream, path, destination, size):
            result = original(budget, stream, path, destination, size)
            if path.endswith("model.onnx"):
                (self.source / path).write_bytes(b"mutated during capture")
            return result

        with patch.object(artifacts._Budget, "copy", mutate), self.assertRaises(ConflictError):
            self.do_import()
        self.assertEqual(self.counts(), (0, 0))
        self.assertFalse(self.store.root.exists())

    def test_limits_bound_files_total_bytes_config_and_depth(self):
        for limits in (
            CaptureLimits(max_files=1),
            CaptureLimits(max_bytes=1),
            CaptureLimits(max_file_bytes=1),
            CaptureLimits(max_config_bytes=1),
            CaptureLimits(max_depth=1),
        ):
            with self.subTest(limits=limits), self.assertRaises(BadRequestError):
                self.do_import(limits=limits)
        self.assertEqual(self.counts(), (0, 0))

    def test_unsupported_multi_source_and_unexpected_fields_are_rejected(self):
        with self.assertRaises(BadRequestError):
            self.do_import(source=[DirectorySource(self.source)])
        data = request().model_dump()
        data["sources"] = ["s3://one", "s3://two"]
        with self.assertRaises(ValidationError):
            PreparedImportRequest(**data)

    def test_current_authorization_denies_non_engineers_viewers_and_stale_roles(self):
        for claims in (self.other, {"email": "user4@test.invalid", "role": "admin"}):
            with self.assertRaises(ForbiddenError):
                self.do_import(claims=claims)
        user = self.session.get(UserEntity, 2)
        user.is_active = False
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            self.do_import()
        self.assertEqual(self.counts(), (0, 0))

    def test_membership_is_rechecked_after_capture(self):
        class RevokingSource(DirectorySource):
            def capture(inner, destination, limits):
                result = super().capture(destination, limits)
                teams.set_member(self.session, self.admin, self.group.id, 2, active=False)
                return result

        with self.assertRaises(ForbiddenError):
            self.do_import(source=RevokingSource(self.source))
        self.assertEqual(self.counts(), (0, 0))

    def test_task_import_requires_current_assignment_revision_and_retains_missing_inputs(self):
        task = preparation.create_task(
            self.session, self.admin, TaskCreate(title="Prepare", missing_inputs=["test_data"])
        )
        req = request(task_id=task.id, expected_task_revision=1, missing_evaluation_inputs=())
        with self.assertRaises(ForbiddenError):
            self.do_import(req)
        task = preparation.claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=1))
        with self.assertRaises(ConflictError):
            self.do_import(req)
        result = self.do_import(req.model_copy(update={"expected_task_revision": 2}))
        self.assertEqual(result.task_id, task.id)
        self.assertIn("test_data", result.missing_evaluation_inputs)
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).state, "claimed")

    def test_changed_metadata_or_completeness_conflicts_without_overwriting_existing_import(self):
        original = self.do_import()
        with self.assertRaises(ConflictError):
            self.do_import(request(missing_evaluation_inputs=()))
        (self.source / "lineage.json").write_bytes(b"new provenance sidecar")
        with self.assertRaises(ConflictError):
            self.do_import()
        self.assertEqual(get_import(self.session, self.engineer, original.candidate.origin, self.store), original)

    def test_storage_failure_rolls_back_candidate_and_receipt_and_retry_recovers(self):
        with patch.object(self.store, "publish", side_effect=OSError("fixture storage failure")):
            with self.assertRaises(ServiceUnavailableError):
                self.do_import()
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.do_import().candidate.origin, request().candidate_origin)

    def test_caller_owned_transaction_can_rollback_capture_registration(self):
        self.do_import(commit=False)
        self.session.rollback()
        self.assertEqual(self.counts(), (0, 0))
        self.assertEqual(self.do_import().candidate.origin, request().candidate_origin)

    def test_corrupt_retained_bytes_are_detected_on_read_and_repeat(self):
        result = self.do_import()
        target = self.store.root / result.artifacts.reference["snapshot_id"] / "repository/iris/1/model.onnx"
        target.chmod(0o644)  # Simulate privileged storage corruption, not an application mutation API.
        target.write_bytes(b"corrupt")
        with self.assertRaises(ConflictError):
            get_import(self.session, self.engineer, result.candidate.origin, self.store)
        with self.assertRaises(ConflictError):
            self.do_import()

    def test_additive_upgrade_preserves_imports_and_receipts_are_append_only(self):
        result = self.do_import()
        self.session.rollback()
        create_tables(self.engine)
        self.assertEqual(get_import(self.session, self.engineer, result.candidate.origin, self.store), result)
        self.session.rollback()
        with self.assertRaises(DBAPIError):
            self.session.execute(text("DELETE FROM candidate_imports"))
        self.session.rollback()
        self.assertEqual(self.counts(), (1, 1))

    def test_read_access_is_rechecked_after_membership_revocation(self):
        result = self.do_import()
        teams.set_member(self.session, self.admin, self.group.id, 2, active=False)
        with self.assertRaises(ForbiddenError):
            get_import(self.session, self.engineer, result.candidate.origin, self.store)
        self.assertEqual(get_import(self.session, self.admin, result.candidate.origin, self.store), result)

    def test_golden_manual_fixture_matches_shared_contract_and_canonical_hashes(self):
        fixture = Path(__file__).parent / "fixtures/candidate_import"
        req = PreparedImportRequest.model_validate_json((fixture / "request.json").read_bytes())
        expected = json.loads((fixture / "expected.json").read_bytes())
        result = self.do_import(req, source=DirectorySource(fixture / "manual_repository"))
        self.assertEqual(result.inventory.model_dump(mode="json"), expected["inventory"])
        self.assertEqual(result.metadata_inventory.model_dump(mode="json"), expected["metadata_inventory"])
        self.assertEqual(result.candidate.model_dump(mode="json"), expected["candidate"])
        self.assertEqual(result.candidate_digest, expected["candidate_digest"])
        self.assertEqual(inventory_digest(result.inventory), expected["inventory_digest"])

    def test_root_build_hints_are_preserved_and_count_toward_model_identity(self):
        baseline = self.do_import()
        (self.source / "requirements.txt").write_bytes(b"numpy==2.2.6\n")
        changed = self.do_import(request("candidate-2"))
        self.assertNotEqual(baseline.candidate.output_inventory, changed.candidate.output_inventory)
        self.assertIn("requirements.txt", {entry.path for entry in changed.inventory.files})

    def test_directory_named_as_a_regular_model_file_is_rejected(self):
        path = self.source / "iris/1/model.onnx"
        path.unlink()
        path.mkdir()
        (path / "unexpected").write_bytes(b"not an ONNX file")
        with self.assertRaises(BadRequestError):
            self.do_import()

    def test_membership_revoked_during_storage_prevents_registration(self):
        publish = self.store.publish

        def revoke(*args, **kwargs):
            result = publish(*args, **kwargs)
            teams.set_member(self.session, self.admin, self.group.id, 2, active=False)
            return result

        with patch.object(self.store, "publish", side_effect=revoke), self.assertRaises(ForbiddenError):
            self.do_import()
        self.assertEqual(self.counts(), (0, 0))

    def test_checked_in_wire_schemas_match_current_models(self):
        from app.schemas.candidate_import import ImportedCandidate

        directory = Path(__file__).resolve().parents[2] / "contracts/candidate-import/v1"
        for model in (PreparedImportRequest, ImportedCandidate):
            expected = json.loads((directory / (model.__name__ + ".schema.json")).read_bytes())
            self.assertEqual(model.model_json_schema(), expected)

    def test_directory_source_and_store_must_not_overlap(self):
        with self.assertRaises(BadRequestError):
            self.do_import(store=DirectoryArtifactStore(self.source / "snapshots"))

    def test_symlink_replacement_between_stat_and_open_cannot_read_outside_root(self):
        outside = self.root / "private"
        outside.write_bytes(b"private fixture bytes")
        target = self.source / "iris/1/model.onnx"
        original_open = os.open
        replaced = False

        def swap(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "model.onnx" and not replaced and "dir_fd" in kwargs:
                replaced = True
                target.unlink()
                target.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)

        with patch("app.services.candidates.artifacts.os.open", side_effect=swap), self.assertRaises(BadRequestError):
            self.do_import()
        self.assertTrue(replaced)
        self.assertEqual(self.counts(), (0, 0))
        self.assertFalse(self.store.root.exists())

    def test_invalid_source_and_task_contract_combinations_are_rejected(self):
        valid = request().model_dump()
        for updates in (
            {"manual_source_origin": None},
            {
                "attempts": [
                    {
                        "origin": {"installation_id": "dev", "record_id": "conversion"},
                        "operation": "conversion",
                        "input_inventory": "sha256:" + "a" * 64,
                        "output_inventory": "sha256:" + "b" * 64,
                    }
                ]
            },
            {"expected_task_revision": 1},
            {"capture_origin": {"installation_id": "another-installation", "record_id": "capture"}},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                PreparedImportRequest(**{**valid, **updates})
        with self.assertRaises(ValueError):
            CaptureLimits(max_files=0)

    def test_cancelled_task_keeps_capture_available_for_admin_audit_but_rejects_new_import(self):
        task = preparation.create_task(self.session, self.admin, TaskCreate(title="Prepare"))
        task = preparation.claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=1))
        req = request(task_id=task.id, expected_task_revision=2)
        result = self.do_import(req)
        preparation.cancel(self.session, self.admin, task.id, ReasonRequest(expected_revision=2, reason="Cancelled"))
        self.assertEqual(get_import(self.session, self.admin, result.candidate.origin, self.store), result)
        with self.assertRaises(ConflictError):
            self.do_import(req)
        self.assertEqual(self.counts(), (1, 1))

    def test_two_independent_models_and_backend_fork_share_one_source_root(self):
        (self.source / "second/1").mkdir(parents=True)
        (self.source / "second/config.pbtxt").write_bytes(b'name: "second" backend: "python"')
        (self.source / "second/1/model.py").write_bytes(b"# fixture; never executed")
        result = self.do_import()
        self.assertEqual([m.name for m in result.models], ["iris", "second"])
        self.assertEqual(result.models[1].backend, "python")


class DirectoryEnumerationTests(unittest.TestCase):
    def test_path_budget_stops_enumeration_before_buffering_all_names(self):
        consumed = 0
        closed = False

        @contextmanager
        def scan(_):
            nonlocal closed

            def entries():
                nonlocal consumed
                for index in range(10000):
                    consumed += 1
                    yield SimpleNamespace(name=f"file-{index}")

            try:
                yield entries()
            finally:
                closed = True

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as staging:
            with patch("app.services.candidates.artifacts.os.scandir", scan):
                with self.assertRaisesRegex(BadRequestError, "Too many artifact paths"):
                    DirectorySource(Path(root)).capture(Path(staging), CaptureLimits(max_files=2))
        self.assertEqual(consumed, 7)  # Existing path budget: three nodes per allowed file, plus the rejected node.
        self.assertTrue(closed)

    def test_deadline_stops_enumeration_before_buffering_all_names(self):
        clock = [0.0]
        consumed = 0
        closed = False

        @contextmanager
        def scan(_):
            nonlocal closed

            def entries():
                nonlocal consumed
                for index in range(10000):
                    consumed += 1
                    clock[0] = 1.0 if index == 0 else 3.0
                    yield SimpleNamespace(name=f"file-{index}")

            try:
                yield entries()
            finally:
                closed = True

        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as staging:
            with (
                patch("app.services.candidates.artifacts.os.scandir", scan),
                patch("app.services.candidates.artifacts.time.monotonic", side_effect=lambda: clock[0]),
            ):
                with self.assertRaisesRegex(ConflictError, "deadline exceeded"):
                    DirectorySource(Path(root)).capture(Path(staging), CaptureLimits(deadline_seconds=2))
        self.assertEqual(consumed, 2)
        self.assertTrue(closed)
