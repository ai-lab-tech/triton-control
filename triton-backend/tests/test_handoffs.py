"""Handoff authorization, atomicity, retries, source verification and API contracts."""

import hashlib
import io
import unittest
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import select

from app.api.handoffs_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import HandoffEntity, HandoffNotificationEntity, PreparationTaskEntity, S3ProfileEntity
from app.exceptions import BadGatewayError, BadRequestError, ConflictError, ForbiddenError, NotFoundError
from app.schemas.handoffs import HandoffRequest, ResolvedInput, S3Input
from app.schemas.preparation import InformationRequest, ResolveInputsRequest
from app.services import handoff_sources, handoffs, preparation, teams
from tests.test_preparation import PreparationFixture


class HandoffFixture(PreparationFixture):
    def seed(self):
        super().seed()
        self.session.add(
            S3ProfileEntity(
                id=1,
                owner_user_id=1,
                name="test",
                endpoint="https://s3.test.invalid",
                bucket="models",
                prefix="allowed",
                access_key="key",
                secret_key_hash="hash",
                secret_key_enc="encrypted",
            )
        )
        self.session.commit()

    def request(self, **changes):
        return HandoffRequest.model_validate(
            {
                "origin": {"installation_id": "dev-lab", "record_id": "iris"},
                "revision": "1",
                "title": "Prepare Iris",
                "source": {"profile_id": 1, "path": "model.onnx"},
                **changes,
            }
        )

    def resolved(self, session, source):
        return ResolvedInput(
            source=source,
            bucket="models",
            key="allowed/" + source.path,
            endpoint="https://s3.test.invalid",
            version_id=source.version_id or "version-1",
            file={"path": source.path, "size": 5, "digest": "sha256:" + hashlib.sha256(b"model").hexdigest()},
        )

    def submit(self, request=None, claims=None):
        with patch.object(handoff_sources, "resolve", side_effect=self.resolved):
            return handoffs.submit(self.session, claims or self.submitter, request or self.request())


class HandoffTests(HandoffFixture, unittest.TestCase):
    def test_changed_record_label_reuses_source_without_provider_reads(self):
        first = self.submit()
        renamed = self.request(origin={"installation_id": "dev-lab", "record_id": "another-label"})
        with patch.object(handoff_sources, "resolve", side_effect=AssertionError("must reuse captured source")):
            second = handoffs.submit(self.session, self.submitter, renamed)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.request.origin.record_id, "iris")
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(HandoffNotificationEntity)).all()), 2)

    def test_different_sources_with_same_origin_and_revision_are_distinct(self):
        first = self.submit()
        second = self.submit(self.request(source={"profile_id": 1, "path": "different.onnx"}))
        self.assertNotEqual(first.task.id, second.task.id)

    def test_explicit_versions_and_origin_installations_are_distinct(self):
        first = self.submit(self.request(source={"profile_id": 1, "path": "model.onnx", "version_id": "v1"}))
        second = self.submit(self.request(source={"profile_id": 1, "path": "model.onnx", "version_id": "v2"}))
        other = self.submit(self.request(origin={"installation_id": "other-lab", "record_id": "iris"}))
        self.assertEqual(len({first.task.id, second.task.id, other.task.id}), 3)

    def test_profile_aliases_reuse_captured_source(self):
        first = self.submit()
        original = self.session.get(S3ProfileEntity, 1)
        self.session.add(S3ProfileEntity.model_validate({**original.model_dump(), "id": 2, "name": "alias"}))
        self.session.commit()
        request = self.request(source={"profile_id": 2, "path": "model.onnx"})
        with patch.object(handoff_sources, "resolve", side_effect=AssertionError("must reuse captured source")):
            second = handoffs.submit(self.session, self.submitter, request)
        self.assertEqual(first.id, second.id)

    def test_changed_requirements_conflict_even_with_new_record_label(self):
        self.submit()
        with self.assertRaises(ConflictError):
            self.submit(self.request(
                origin={"installation_id": "dev-lab", "record_id": "another-label"},
                acceptance_criteria="changed",
            ))

    def test_legacy_receipt_is_found_without_rewriting_its_identity(self):
        from app.repositories import handoffs as repository
        from app.services.lineage import digest

        original = repository.add
        legacy_identity = digest({"origin": self.request().origin.model_dump(), "revision": "1"})

        def legacy_add(session, row, recipients):
            row.identity_digest = legacy_identity
            original(session, row, recipients)

        with patch.object(repository, "add", side_effect=legacy_add):
            first = self.submit()
        with patch.object(handoff_sources, "resolve", side_effect=AssertionError("must reuse legacy capture")):
            second = handoffs.submit(self.session, self.submitter, self.request(
                origin={"installation_id": "dev-lab", "record_id": "new-label"},
            ))
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.session.get(HandoffEntity, first.id).identity_digest, legacy_identity)

    def test_handoff_evaluation_blockers_cannot_be_cleared_without_inputs(self):
        receipt = self.submit()
        for actor in (self.admin, self.engineer):
            task = preparation.get_task(self.session, actor, receipt.task.id)
            if actor == self.engineer:
                task = self.claim(task)
            events = preparation.list_events(self.session, actor, task.id)
            with self.assertRaises(ConflictError):
                preparation.resolve_inputs(self.session, actor, task.id, ResolveInputsRequest(
                    expected_revision=task.revision, resolved_inputs=task.missing_inputs, reason="done",
                ))
            self.session.rollback()
            current = handoffs.get(self.session, actor, receipt.id)
            self.assertEqual(current.task.missing_inputs, task.missing_inputs)
            self.assertEqual(current.task.revision, task.revision)
            self.assertEqual(len(preparation.list_events(self.session, actor, task.id)), len(events))

    def test_historical_cleared_flags_still_show_receipt_blockers_in_queue_and_detail(self):
        receipt = self.submit()
        row = self.session.get(PreparationTaskEntity, receipt.task.id)
        row.missing_inputs = []
        self.session.add(row)
        self.session.commit()
        for task in (
            handoffs.get(self.session, self.submitter, receipt.id).task,
            preparation.get_task(self.session, self.engineer, receipt.task.id),
            preparation.list_tasks(self.session, self.engineer)[0],
        ):
            self.assertEqual(set(task.missing_inputs), set(receipt.task.missing_inputs))
            self.assertIn("missing_input:evaluation_dataset", task.blocked_reasons)

    def test_other_task_checklist_items_can_still_be_resolved(self):
        receipt = self.submit()
        task = preparation.request_information(self.session, self.admin, receipt.task.id, InformationRequest(
            expected_revision=1, missing_inputs=["license_review"], reason="review license",
        ))
        task = preparation.resolve_inputs(self.session, self.admin, task.id, ResolveInputsRequest(
            expected_revision=task.revision, resolved_inputs=["license_review"], reason="reviewed",
        ))
        self.assertNotIn("license_review", task.missing_inputs)
        self.assertEqual(set(task.missing_inputs), set(receipt.task.missing_inputs))

    def test_new_complete_revision_keeps_old_receipt_incomplete(self):
        first = self.submit()
        second = self.submit(self.request(
            revision="2", dataset={"profile_id": 1, "path": "data.csv"},
            baseline={"profile_id": 1, "path": "baseline.json"},
            expected_behavior="correct class", acceptance_criteria="accuracy above 95%",
        ))
        self.assertEqual(second.task.missing_inputs, [])
        self.assertEqual(handoffs.get(self.session, self.submitter, first.id).task.missing_inputs,
                         first.task.missing_inputs)

    def test_submission_persists_exact_source_task_event_and_private_notifications(self):
        receipt = self.submit()
        self.assertEqual(receipt.source.version_id, "version-1")
        self.assertEqual(receipt.source_snapshot.inventory[:7], "sha256:")
        self.assertEqual(receipt.task.assigned_team_id, self.team.id)
        self.assertEqual(len(preparation.list_events(self.session, self.engineer, receipt.task.id)), 1)
        for claims in (self.engineer, self.claims(3)):
            notifications = handoffs.notifications(self.session, claims, 100, 0)
            self.assertEqual(len(notifications), 1)
            self.assertEqual(notifications[0].handoff_id, receipt.id)
            serialized = notifications[0].model_dump_json()
            for private in ("model.onnx", "Prepare Iris", "encrypted", "s3.test.invalid"):
                self.assertNotIn(private, serialized)
        self.assertEqual(handoffs.notifications(self.session, self.submitter, 100, 0), [])

    def test_retry_returns_same_receipt_without_resolving_mutable_sources_again(self):
        first = self.submit()
        with patch.object(handoff_sources, "resolve", side_effect=AssertionError("must reuse receipt")):
            second = handoffs.submit(self.session, self.submitter, self.request())
        self.assertEqual((first.id, first.task.id), (second.id, second.task.id))
        self.assertEqual(len(self.session.exec(select(HandoffEntity)).all()), 1)
        self.assertEqual(len(self.session.exec(select(HandoffNotificationEntity)).all()), 2)

    def test_same_identity_changed_content_conflicts(self):
        self.submit()
        with self.assertRaises(ConflictError):
            self.submit(self.request(acceptance_criteria="different criteria"))
        self.assertEqual(len(self.session.exec(select(PreparationTaskEntity)).all()), 1)

    def test_new_revision_creates_new_task(self):
        first, second = self.submit(), self.submit(self.request(revision="2"))
        self.assertNotEqual(first.task.id, second.task.id)

    def test_missing_inputs_block_readiness_but_accept_task(self):
        receipt = self.submit()
        self.assertEqual(
            set(receipt.task.missing_inputs),
            {"evaluation_dataset", "baseline", "expected_behavior", "acceptance_criteria"},
        )
        self.assertIn("missing_input:evaluation_dataset", receipt.task.blocked_reasons)

    def test_complete_inputs_have_no_missing_evaluation_fields(self):
        receipt = self.submit(
            self.request(
                dataset={"profile_id": 1, "path": "data.csv"},
                baseline={"profile_id": 1, "path": "baseline.json"},
                expected_behavior="Correct class",
                acceptance_criteria="Accuracy >= 95%",
            )
        )
        self.assertEqual(receipt.task.missing_inputs, [])
        self.assertIsNotNone(receipt.dataset)
        self.assertIsNotNone(receipt.baseline)

    def test_empty_engineering_group_keeps_visible_blocked_task_without_notifications(self):
        for user in (2, 3):
            teams.set_member(self.session, self.admin, self.team.id, user, active=False)
        receipt = self.submit()
        self.assertIn("no_eligible_engineers", receipt.task.blocked_reasons)
        self.assertEqual(self.session.exec(select(HandoffNotificationEntity)).all(), [])

    def test_notification_failure_rolls_back_receipt_task_and_event(self):
        with patch("app.repositories.handoffs.add", side_effect=RuntimeError("database interrupted")):
            with self.assertRaises(RuntimeError):
                self.submit()
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])
        self.assertEqual(self.session.exec(select(HandoffEntity)).all(), [])
        self.assertIsNotNone(self.submit())

    def test_failure_after_notification_insert_rolls_back_all_rows(self):
        from app.repositories import handoffs as repository

        original = repository.add

        def interrupted(*args):
            original(*args)
            raise RuntimeError("commit interrupted")

        with patch.object(repository, "add", side_effect=interrupted):
            with self.assertRaises(RuntimeError):
                self.submit()
        for entity in (HandoffEntity, HandoffNotificationEntity, PreparationTaskEntity):
            self.assertEqual(self.session.exec(select(entity)).all(), [])

    def test_source_failure_creates_nothing(self):
        with patch.object(handoff_sources, "resolve", side_effect=ForbiddenError("denied")):
            with self.assertRaises(ForbiddenError):
                handoffs.submit(self.session, self.submitter, self.request())
        self.assertEqual(self.session.exec(select(PreparationTaskEntity)).all(), [])

    def test_unavailable_profile_is_denied_before_source_capture(self):
        with self.assertRaises(ForbiddenError):
            self.submit(self.request(source={"profile_id": 999, "path": "private"}))

    def test_viewer_and_delegated_tokens_cannot_submit(self):
        for claims in (self.claims(5), {**self.submitter, "token_use": "workspace_deployment"}):
            with self.assertRaises(ForbiddenError):
                self.submit(claims=claims)

    def test_unrelated_member_cannot_read_receipt_or_retry_it(self):
        receipt = self.submit()
        with self.assertRaises(ForbiddenError):
            handoffs.get(self.session, self.claims(1) | {"email": "user5@test.invalid"}, receipt.id)
        teams.set_member(self.session, self.admin, self.team.id, 3, active=False)
        with self.assertRaises(ForbiddenError):
            handoffs.get(self.session, self.claims(3), receipt.id)
        with self.assertRaises(ForbiddenError):
            self.submit(claims=self.claims(3))

    def test_notifications_are_recipient_scoped_and_mark_read_is_idempotent(self):
        self.submit()
        notification = handoffs.notifications(self.session, self.engineer, 100, 0)[0]
        with self.assertRaises(NotFoundError):
            handoffs.read_notification(self.session, self.submitter, notification.id)
        first = handoffs.read_notification(self.session, self.engineer, notification.id)
        second = handoffs.read_notification(self.session, self.engineer, notification.id)
        self.assertEqual(first.read_at, second.read_at)

    def test_http_contract_and_validation(self):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.submitter
        with TestClient(app) as client, patch.object(handoff_sources, "resolve", side_effect=self.resolved):
            response = client.post("/api/preparation/handoffs", json=self.request().model_dump(mode="json"))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(client.get("/api/preparation/handoffs/" + response.json()["id"]).status_code, 200)
            self.assertEqual(client.post("/api/preparation/handoffs", json={}).status_code, 422)


class SourceTests(HandoffFixture, unittest.TestCase):
    def client(self, version="v1", data=b"model", size=5):
        client = MagicMock()
        client.head_object.return_value = {"VersionId": version, "ContentLength": size}
        client.get_object.return_value = {"VersionId": version, "Body": io.BytesIO(data)}
        return client

    def resolve(self, client, **changes):
        with patch.object(handoff_sources, "build_s3_client", return_value=client):
            return handoff_sources.resolve(self.session, S3Input(profile_id=1, path="model.onnx", **changes))

    def test_measures_sha256_and_pins_resolved_version(self):
        client = self.client()
        result = self.resolve(client)
        self.assertEqual(result.file.digest, "sha256:" + hashlib.sha256(b"model").hexdigest())
        client.get_object.assert_called_once_with(Bucket="models", Key="allowed/model.onnx", VersionId="v1")
        self.assertTrue(client.get_object.return_value["Body"].closed)
        client.close.assert_called_once()

    def test_explicit_version_is_used_in_resolution(self):
        client = self.client()
        self.resolve(client, version_id="v1")
        client.head_object.assert_called_once_with(Bucket="models", Key="allowed/model.onnx", VersionId="v1")

    def test_unversioned_source_fails_without_task(self):
        for version in (None, "null"):
            with self.assertRaises(BadRequestError):
                self.resolve(self.client(version=version))

    def test_checksum_mismatch_and_size_bounds(self):
        for client, changes in [
            (self.client(), {"expected_digest": "sha256:" + "a" * 64}),
            (self.client(size=2), {}),
            (self.client(size=handoff_sources.MAX_BYTES + 1), {}),
        ]:
            with self.assertRaises(BadRequestError):
                self.resolve(client, **changes)

    def test_access_denied_is_sanitized(self):
        client = self.client()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "SECRET"}}, "HeadObject"
        )
        with self.assertRaises(ForbiddenError) as context:
            self.resolve(client)
        self.assertNotIn("SECRET", str(context.exception))

    def test_transport_failure_is_sanitized(self):
        client = self.client()
        client.get_object.side_effect = OSError("SECRET")
        with self.assertRaises(BadGatewayError) as context:
            self.resolve(client)
        self.assertNotIn("SECRET", str(context.exception))

    def test_deadline_stops_reading_and_closes_body(self):
        client = self.client()
        with patch.object(handoff_sources.time, "monotonic", side_effect=[0, 121]):
            with self.assertRaises(BadRequestError):
                self.resolve(client)
        self.assertTrue(client.get_object.return_value["Body"].closed)

    def test_traversal_and_absolute_paths_are_rejected(self):
        for path in ("../private", "/outside", "a/../private", "a\\private"):
            with self.assertRaises(BadRequestError):
                handoff_sources.authorize(self.session, S3Input(profile_id=1, path=path))
