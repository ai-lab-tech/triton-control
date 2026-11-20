"""S05-D portable references, storage relocation and authorization contracts."""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError
from sqlmodel import select

from app.core import installation_policy
from app.db.entities import HandoffEntity
from app.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.schemas.control_lineage import ControlLineage
from app.schemas.handoffs import ResolvedInput, ResolvedRepository, S3Input
from app.services import handoff_repositories, handoffs
from tests.test_handoffs import HandoffFixture

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "contracts/lineage/v1/control-lineage.fixture.json").read_text())


class ControlLineageTests(HandoffFixture, unittest.TestCase):
    def request_with_lineage(self, path="repo", **changes):
        return self.request(origin=FIXTURE["lineage"]["origin"], lineage=FIXTURE["lineage"],
                            source={"kind": "s3_repository", "profile_id": 1, "path": path}, **changes)

    def repository(self, session, source, **kwargs):
        return ResolvedRepository(source=source, bucket="models", key="allowed/" + source.path,
                                  endpoint="https://s3.test.invalid", files=[ResolvedInput(
                                      source=S3Input(profile_id=1, path=source.path + "/" + file["path"]),
                                      bucket="models", key="allowed/" + source.path + "/" + file["path"],
                                      endpoint="https://s3.test.invalid", version_id="pinned", file=file,
                                  ) for file in FIXTURE["manifest"]["inventories"][1]["files"]])

    def test_async_ready_capture_preserves_lineage_and_retry_identity(self):
        from contextlib import nullcontext
        from datetime import datetime
        from sqlmodel import Session
        from app.repositories import operations
        from app.services import handoff_capture
        from app.services.operations import OperationService, OperationWorker
        service = OperationService()
        service.register(handoff_capture.KIND, handoff_capture.adapter)
        clock = (patch.object(operations, "now", lambda _: datetime.utcnow())
                 if self.engine.dialect.name == "sqlite" else nullcontext())
        with clock, patch.object(handoff_capture, "service", service), \
                patch.object(handoff_repositories, "resolve", side_effect=self.repository):
            first = handoff_capture.submit(self.session, self.submitter, self.request_with_lineage(), "ready-1")
            OperationWorker(service, lambda: Session(self.engine)).run_once()
            repeated = handoff_capture.submit(self.session, self.submitter, self.request_with_lineage(), "ready-1")
            self.assertEqual(first.id, repeated.id)
            self.assertEqual(repeated.state, "succeeded")
            row = self.session.exec(select(HandoffEntity)).one()
            self.assertEqual(row.payload["request"]["lineage"], FIXTURE["lineage"])

    def test_relocated_source_reuses_receipt_without_losing_model_identity(self):
        with patch.object(handoff_repositories, "resolve", side_effect=self.repository) as provider:
            first = handoffs.submit(self.session, self.submitter, self.request_with_lineage())
            second = handoffs.submit(self.session, self.submitter, self.request_with_lineage("moved"))
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.source_snapshot.kind, "mlflow")
        self.assertEqual(first.source_snapshot.origin, first.request.lineage.origin)
        self.assertEqual(first.request.lineage.model_dump(mode="json"), FIXTURE["lineage"])
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)

    def test_relocation_does_not_allow_rewriting_lineage_or_requirements(self):
        with patch.object(handoff_repositories, "resolve", side_effect=self.repository):
            handoffs.submit(self.session, self.submitter, self.request_with_lineage())
            with self.assertRaises(ConflictError):
                handoffs.submit(self.session, self.submitter, self.request_with_lineage("moved", title="Changed"))

    def test_captured_inventory_must_match_the_declared_candidate(self):
        def changed(session, source, **kwargs):
            result = self.repository(session, source)
            result.files[0].file = result.files[0].file.model_copy(update={"digest": "sha256:" + "0" * 64})
            return result
        with patch.object(handoff_repositories, "resolve", side_effect=changed):
            with self.assertRaisesRegex(BadRequestError, "inventory"):
                handoffs.submit(self.session, self.submitter, self.request_with_lineage())
        self.assertEqual(self.session.exec(select(HandoffEntity)).all(), [])

    def test_viewers_delegated_tokens_and_production_cannot_submit(self):
        provider = self.enterContext(patch.object(handoff_repositories, "resolve"))
        for claims in ({"email": "user5@test.invalid", "role": "viewer"},
                       {**self.submitter, "token_use": "mlflow_workflow"}):
            with self.assertRaises(ForbiddenError):
                handoffs.submit(self.session, claims, self.request_with_lineage())
        with patch.object(installation_policy, "mode", return_value="production"):
            with self.assertRaises(ForbiddenError):
                handoffs.submit(self.session, self.admin, self.request_with_lineage())
        provider.assert_not_called()


class ControlLineageSchemaTests(unittest.TestCase):
    def test_fixture_is_valid(self):
        # Arrange
        payload = copy.deepcopy(FIXTURE["lineage"])
        # Act
        lineage = ControlLineage.model_validate(payload)
        # Assert
        self.assertEqual(lineage.model_dump(mode="json"), payload)

    def test_rejects_inconsistent_lineage(self):
        cases = [
            ("approval", ("production_approval",), True),
            ("source installation", ("source", "installation_id"), "other"),
            ("candidate installation", ("candidate", "origin", "installation_id"), "other"),
            ("conversion installation", ("candidate", "attempts", 0, "origin", "installation_id"), "other"),
            ("conversion run", ("conversion_run_id",), "missing-run"),
            ("conversion operation", ("candidate", "attempts", 0, "operation"), "validation"),
            ("predecessor", ("previous_serving_model", "candidate_origin", "record_id"), "other"),
        ]
        for name, path, value in cases:
            with self.subTest(name=name):
                # Arrange
                payload = copy.deepcopy(FIXTURE["lineage"])
                target = payload
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                # Act / Assert
                with self.assertRaises(ValidationError):
                    ControlLineage.model_validate(payload)

    def test_shared_schema_stays_current(self):
        # Arrange
        path = Path(__file__).resolve().parents[2] / "contracts/lineage/v1/control-lineage.schema.json"
        # Act
        schema = ControlLineage.model_json_schema()
        # Assert
        self.assertEqual(json.loads(path.read_text()), schema)
