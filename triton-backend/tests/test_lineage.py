"""Arrange–Act–Assert tests for canonical identity and immutable publication."""

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError
from app.schemas.lineage import EngineeringAttempt, FileEntry, InventoryV1, Origin, ServingCandidateV1, Snapshot
from app.services.lineage import inventory_digest, publish
from app.services.lineage_inventory import measure


def candidate(inventory, record="candidate", installation="dev", operation="packaging"):
    identity = inventory_digest(inventory)
    return ServingCandidateV1(
        origin=Origin(installation_id=installation, record_id=record),
        derived_from_snapshot=Snapshot(
            origin=Origin(installation_id="source", record_id="snapshot"), kind="manual", inventory=identity
        ),
        attempts=(
            EngineeringAttempt(
                origin=Origin(installation_id=installation, record_id="attempt"),
                operation=operation,
                input_inventory=identity,
                output_inventory=identity,
            ),
        ),
        output_inventory=identity,
    )


class LineageTests(unittest.TestCase):
    def test_inventory_order_is_irrelevant_but_weight_bytes_matter(self):
        # Arrange
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "weights").write_bytes(b"original")
            (root / "config").write_bytes(b"config")
            first = measure(root)
            # Act
            reordered = InventoryV1(files=tuple(reversed(first.files)))
            (root / "weights").write_bytes(b"new weights")
            changed = measure(root)
            # Assert
            self.assertEqual(inventory_digest(first), inventory_digest(reordered))
            self.assertNotEqual(inventory_digest(first), inventory_digest(changed))

    def test_unsafe_and_duplicate_paths_are_rejected(self):
        # Arrange
        for path in ("../weights", "/weights", "a//b", "a/./b", "a\\b"):
            # Act / Assert
            with self.subTest(path=path), self.assertRaises(ValidationError):
                FileEntry(path=path, size=1, digest="sha256:" + "a" * 64)
        entry = FileEntry(path="weights", size=1, digest="sha256:" + "a" * 64)
        with self.assertRaises(ValidationError):
            InventoryV1(files=(entry, entry))

    def test_symlink_is_not_inventoried(self):
        # Arrange
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "link").symlink_to("/etc/passwd")
            # Act / Assert
            with self.assertRaises(ValueError):
                measure(root)

    def test_manual_forks_and_cross_installation_origins(self):
        # Arrange
        inventory = InventoryV1(files=())
        # Act
        onnx = candidate(inventory, "onnx")
        trt = candidate(inventory, "tensorrt", operation="conversion")
        copied = candidate(inventory, "copy", installation="other")
        # Assert
        self.assertEqual(onnx.derived_from_snapshot, trt.derived_from_snapshot)
        self.assertEqual(onnx.output_inventory, copied.output_inventory)
        self.assertNotEqual(onnx.origin, copied.origin)
        self.assertIsNone(trt.previous_serving_candidate)
        self.assertEqual(onnx.plugin_references, ())

    def test_publication_is_idempotent_conflicts_and_denies_viewers(self):
        # Arrange
        engine = create_engine("sqlite://")
        create_tables(engine)
        inventory = InventoryV1(files=())
        original = candidate(inventory)
        with Session(engine) as session:
            # Act
            first = publish(session, {"role": "admin"}, original, (inventory,))
            session.commit()
            repeated = publish(session, {"role": "admin"}, original, (inventory,))
            # Assert
            self.assertEqual(first, repeated)
            with self.assertRaises(ConflictError):
                publish(session, {"role": "admin"}, candidate(inventory, operation="conversion"), (inventory,))
            with self.assertRaises(ForbiddenError):
                publish(session, {"role": "viewer"}, original, (inventory,))
        engine.dispose()

    def test_runtime_only_release_reuses_candidate(self):
        # Arrange
        from app.schemas.lineage import ReleaseLineageV1
        from app.services.lineage import digest

        prepared = candidate(InventoryV1(files=()))
        first = ReleaseLineageV1(
            origin=Origin(installation_id="prod", record_id="r1"),
            candidate=prepared.origin,
            candidate_digest=digest(prepared.model_dump(mode="json")),
            runtime_image="registry/runtime@sha256:" + "a" * 64,
        )
        # Act
        second = ReleaseLineageV1(
            origin=Origin(installation_id="prod", record_id="r2"),
            candidate=first.candidate,
            candidate_digest=first.candidate_digest,
            runtime_image="registry/runtime@sha256:" + "b" * 64,
            replaces_release=first.origin,
        )
        # Assert
        self.assertEqual(first.candidate, second.candidate)
        self.assertEqual(first.candidate_digest, second.candidate_digest)
        self.assertEqual(second.replaces_release, first.origin)
        self.assertNotEqual(first.runtime_image, second.runtime_image)
