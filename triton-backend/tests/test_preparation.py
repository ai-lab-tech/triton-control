"""Preparation behavior, authorization, persistence and HTTP contracts."""

import unittest
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.api.preparation_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import PreparationAttemptEntity, PreparationTaskEntity, PreparationTaskEventEntity, UserEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.repositories import users
from app.schemas.lineage import EngineeringAttempt
from app.schemas.preparation import (
    InformationRequest,
    ReasonRequest,
    ReassignRequest,
    ResolveInputsRequest,
    TaskCreate,
    TaskRevision,
)
from app.services import preparation, teams


class PreparationFixture:
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.seed()

    def seed(self):
        self.session.add_all(
            [
                UserEntity(id=n, name=f"User {n}", email=f"user{n}@test.invalid", role=role, password_hash="hash")
                for n, role in [(1, "admin"), (2, "member"), (3, "member"), (4, "member"), (5, "viewer")]
            ]
        )
        self.session.commit()
        self.admin = self.claims(1)
        self.team = teams.initialize_engineering_group(self.session, self.admin)
        for user_id in (2, 3):
            teams.set_member(self.session, self.admin, self.team.id, user_id, active=True)
        self.engineer = self.claims(2)
        self.submitter = self.claims(4)

    def claims(self, user_id):
        return {"email": f"user{user_id}@test.invalid", "role": "admin" if user_id == 1 else "member"}

    def create(self, missing_inputs=None, **kwargs):
        return preparation.create_task(
            self.session,
            self.submitter,
            TaskCreate(title="Prepare Iris", missing_inputs=missing_inputs or [], **kwargs),
        )

    def claim(self, task):
        return preparation.claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=task.revision))

    def attempt(self):
        # Actual attempt fixture with measured-inventory-shaped identities; no execution is fabricated by the service.
        return EngineeringAttempt(
            origin={"installation_id": "test-dev", "record_id": "conversion-1"},
            operation="conversion",
            input_inventory="sha256:" + "a" * 64,
            output_inventory="sha256:" + "b" * 64,
        )


class PreparationTests(PreparationFixture, unittest.TestCase):
    def test_create_persists_installation_routing_and_initial_event(self):
        snapshot_id = uuid4()
        task = self.create(["test_data", "test_data", "expected_results"], source_snapshot_id=snapshot_id)
        self.assertEqual(task.assigned_team_id, self.team.id)
        self.assertEqual((task.state, task.revision, task.assignee_user_id), ("queued", 1, None))
        self.assertEqual(task.source_snapshot_id, snapshot_id)
        self.assertEqual(task.missing_inputs, ["expected_results", "test_data"])
        history = preparation.list_events(self.session, self.engineer, task.id)
        self.assertEqual([(e.action, e.revision) for e in history], [("created", 1)])

    def test_missing_inputs_do_not_prevent_claim(self):
        # Arrange
        task = self.create(["test_data"])
        # Act
        result = self.claim(task)
        # Assert
        self.assertEqual(result.state, "claimed")
        self.assertIn("missing_input:test_data", result.blocked_reasons)

    def test_resolving_missing_inputs_retains_claim(self):
        # Arrange
        task = self.claim(self.create(["test_data"]))
        request = ResolveInputsRequest(expected_revision=2, resolved_inputs=["test_data"], reason="Dataset captured")
        # Act
        result = preparation.resolve_inputs(self.session, self.engineer, task.id, request)
        # Assert
        self.assertEqual(result.blocked_reasons, [])
        self.assertEqual(result.state, "claimed")

    def test_information_request_retains_claim_and_records_reason(self):
        # Arrange
        task = self.claim(self.create())
        request = InformationRequest(expected_revision=2, missing_inputs=["test_data"], reason="Need examples")
        # Act
        result = preparation.request_information(self.session, self.engineer, task.id, request)
        history = preparation.list_events(self.session, self.engineer, task.id)
        # Assert
        self.assertEqual((result.state, result.assignee_user_id), ("awaiting_information", 2))
        self.assertEqual([e.action for e in history], ["information_requested", "claimed", "created"])
        self.assertEqual(history[0].changes["reason"], "Need examples")

    def test_information_resolution_returns_to_claimed_and_retains_history(self):
        # Arrange
        task = self.claim(self.create())
        preparation.request_information(
            self.session,
            self.engineer,
            task.id,
            InformationRequest(expected_revision=2, missing_inputs=["test_data"], reason="Need examples"),
        )
        request = ResolveInputsRequest(expected_revision=3, resolved_inputs=["test_data"], reason="Captured inputs")
        # Act
        result = preparation.resolve_inputs(self.session, self.engineer, task.id, request)
        history = preparation.list_events(self.session, self.engineer, task.id)
        # Assert
        self.assertEqual((result.state, result.revision), ("claimed", 4))
        self.assertEqual(
            [e.action for e in history], ["inputs_resolved", "information_requested", "claimed", "created"]
        )

    def test_release_claim_returns_task_to_queue(self):
        # Arrange
        task = self.claim(self.create())
        # Act
        result = preparation.release_claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=2))
        # Assert
        self.assertEqual((result.state, result.assignee_user_id), ("queued", None))

    def test_released_task_can_be_claimed_by_another_engineer(self):
        # Arrange
        task = self.claim(self.create())
        preparation.release_claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=2))
        # Act
        result = preparation.claim(self.session, self.claims(3), task.id, TaskRevision(expected_revision=3))
        # Assert
        self.assertEqual((result.assignee_user_id, result.revision), (3, 4))

    def test_queue_query_count_does_not_grow_with_page_size(self):
        # Arrange
        for _ in range(100):
            self.create(["test_data"])
        statements = []

        def count_queries(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", count_queries)
        try:
            # Act
            small = preparation.list_tasks(self.session, self.engineer, limit=1)
            small_count = len(statements)
            statements.clear()
            large = preparation.list_tasks(self.session, self.engineer, limit=100)
            large_count = len(statements)
        finally:
            event.remove(self.engine, "before_cursor_execute", count_queries)
        # Assert
        self.assertEqual((len(small), len(large)), (1, 100))
        self.assertEqual(large_count, small_count)
        self.assertLessEqual(large_count, 6)
        self.assertTrue(all(row.blocked_reasons == ["missing_input:test_data"] for row in large))

    def test_admin_queue_reports_revoked_assignee_and_empty_team(self):
        # Arrange
        task = self.claim(self.create(["test_data"]))
        for user_id in (2, 3):
            teams.set_member(self.session, self.admin, self.team.id, user_id, active=False)
        # Act
        rows = preparation.list_tasks(self.session, self.admin)
        # Assert
        self.assertEqual([row.id for row in rows], [task.id])
        self.assertEqual(
            rows[0].blocked_reasons,
            ["missing_input:test_data", "no_eligible_engineers", "assignee_ineligible"],
        )

    def test_stale_revision_and_double_claim_do_not_add_events(self):
        task = self.claim(self.create())
        for revision in (1, 2):
            with self.assertRaises(ConflictError):
                preparation.claim(self.session, self.claims(3), task.id, TaskRevision(expected_revision=revision))
            self.session.rollback()
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).assignee_user_id, 2)
        self.assertEqual(len(preparation.list_events(self.session, self.engineer, task.id)), 2)

    def test_queue_filters_before_pagination_and_never_grants_resource_access(self):
        task = self.create()
        outsider = self.claims(5)
        self.assertEqual(preparation.list_tasks(self.session, outsider), [])
        self.assertEqual([r.id for r in preparation.list_tasks(self.session, self.engineer)], [task.id])
        self.assertEqual([r.id for r in preparation.list_tasks(self.session, self.submitter)], [task.id])
        self.assertEqual(preparation.list_tasks(self.session, self.engineer, limit=1, offset=1), [])
        self.assertEqual(self.session.get(UserEntity, 2).assigned_instances, [])
        with self.assertRaises(ForbiddenError):
            preparation.get_task(self.session, outsider, task.id)

    def test_submitter_can_read_and_cancel_but_not_claim_without_membership(self):
        task = self.create()
        self.assertEqual(preparation.get_task(self.session, self.submitter, task.id).id, task.id)
        with self.assertRaises(ForbiddenError):
            preparation.claim(self.session, self.submitter, task.id, TaskRevision(expected_revision=1))
        result = preparation.cancel(
            self.session, self.submitter, task.id, ReasonRequest(expected_revision=1, reason="Superseded")
        )
        self.assertEqual(result.state, "cancelled")

    def test_admin_can_manage_but_cannot_claim_without_engineering_membership(self):
        task = self.create()
        with self.assertRaises(ForbiddenError):
            preparation.claim(self.session, self.admin, task.id, TaskRevision(expected_revision=1))
        result = preparation.reassign(
            self.session,
            self.admin,
            task.id,
            ReassignRequest(expected_revision=1, assignee_user_id=3, reason="Assign engineer"),
        )
        self.assertEqual(result.assignee_user_id, 3)

    def test_other_engineer_cannot_release_resolve_cancel_or_reassign_a_claim(self):
        task = self.claim(self.create(["test_data"]))
        requests: list[tuple[Callable[..., Any], TaskRevision]] = [
            (preparation.release_claim, TaskRevision(expected_revision=2)),
            (
                preparation.resolve_inputs,
                ResolveInputsRequest(expected_revision=2, resolved_inputs=["test_data"], reason="Done"),
            ),
            (preparation.cancel, ReasonRequest(expected_revision=2, reason="Cancel")),
            (preparation.reassign, ReassignRequest(expected_revision=2, assignee_user_id=3, reason="Reassign")),
        ]
        for method, request in requests:
            with self.subTest(method=method.__name__), self.assertRaises(ForbiddenError):
                method(self.session, self.claims(3), task.id, request)
            self.session.rollback()

    def test_empty_team_is_visible_and_recoverable(self):
        for user_id in (2, 3):
            teams.set_member(self.session, self.admin, self.team.id, user_id, active=False)
        task = self.create(["test_data"])
        self.assertIn("no_eligible_engineers", task.blocked_reasons)
        self.assertEqual(preparation.list_tasks(self.session, self.engineer), [])
        teams.set_member(self.session, self.admin, self.team.id, 2, active=True)
        task = self.claim(task)
        self.assertNotIn("no_eligible_engineers", task.blocked_reasons)
        self.assertIn("missing_input:test_data", task.blocked_reasons)

    def test_revocation_denies_assignee_and_admin_can_recover_without_erasing_attempts(self):
        task = self.claim(self.create())
        task = preparation.record_attempt(
            self.session, self.engineer, task.id, TaskRevision(expected_revision=2), self.attempt()
        )
        teams.set_member(self.session, self.admin, self.team.id, 2, active=False)
        for method in (preparation.get_task, preparation.list_events, preparation.list_attempts):
            with self.assertRaises(ForbiddenError):
                method(self.session, self.engineer, task.id)
        self.assertEqual(preparation.list_tasks(self.session, self.engineer), [])
        self.assertIn("assignee_ineligible", preparation.get_task(self.session, self.admin, task.id).blocked_reasons)
        task = preparation.reassign(
            self.session,
            self.admin,
            task.id,
            ReassignRequest(expected_revision=3, assignee_user_id=3, reason="Membership revoked"),
        )
        task = preparation.cancel(
            self.session, self.admin, task.id, ReasonRequest(expected_revision=4, reason="Keep for audit")
        )
        self.assertEqual(len(preparation.list_attempts(self.session, self.admin, task.id)), 1)
        self.assertEqual(len(preparation.list_events(self.session, self.admin, task.id)), 5)
        with self.assertRaises(ConflictError):
            preparation.claim(self.session, self.claims(3), task.id, TaskRevision(expected_revision=5))

    def test_stale_account_role_and_deactivation_are_rechecked(self):
        task = self.create()
        for role, active in [("viewer", True), ("member", False)]:
            user = self.session.get(UserEntity, 2)
            user.role, user.is_active = role, active
            self.session.commit()
            with self.assertRaises(ForbiddenError):
                preparation.claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=1))
            self.session.rollback()
        user = self.session.get(UserEntity, 1)
        user.role = "viewer"
        self.session.commit()
        with self.assertRaises(ForbiddenError):
            preparation.reassign(
                self.session,
                self.admin,
                task.id,
                ReassignRequest(expected_revision=1, assignee_user_id=3, reason="Stale admin token"),
            )

    def test_demoted_submitter_retains_read_access_but_cannot_mutate(self):
        task = self.create()
        user = self.session.get(UserEntity, 4)
        user.role = "viewer"
        self.session.commit()
        self.assertEqual(preparation.get_task(self.session, self.submitter, task.id).id, task.id)
        with self.assertRaises(ForbiddenError):
            preparation.cancel(
                self.session,
                self.submitter,
                task.id,
                ReasonRequest(expected_revision=1, reason="Viewer must not mutate"),
            )

    def test_deleted_assignee_does_not_delete_task_or_history(self):
        task = self.claim(self.create())
        users.delete(self.session, self.session.get(UserEntity, 2))
        result = preparation.get_task(self.session, self.admin, task.id)
        self.assertEqual(result.assignee_user_id, 2)
        self.assertIn("assignee_ineligible", result.blocked_reasons)
        self.assertEqual(preparation.list_events(self.session, self.admin, task.id)[0].actor_user_id, 2)
        result = preparation.reassign(
            self.session,
            self.admin,
            task.id,
            ReassignRequest(expected_revision=2, assignee_user_id=None, reason="Deleted account"),
        )
        self.assertEqual(result.state, "queued")

    def test_invalid_reassignment_and_resolution_leave_state_intact(self):
        task = self.claim(self.create(["test_data"]))
        for user_id in (4, 5, 999):
            with self.assertRaises(ConflictError):
                preparation.reassign(
                    self.session,
                    self.admin,
                    task.id,
                    ReassignRequest(expected_revision=2, assignee_user_id=user_id, reason="Invalid"),
                )
            self.session.rollback()
        with self.assertRaises(ConflictError):
            preparation.resolve_inputs(
                self.session,
                self.engineer,
                task.id,
                ResolveInputsRequest(expected_revision=2, resolved_inputs=["unknown"], reason="Unknown input"),
            )
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).revision, 2)

    def test_attempt_recording_does_not_change_task_state_and_rejects_duplicate_origins(self):
        task = self.claim(self.create())
        task = preparation.record_attempt(
            self.session, self.engineer, task.id, TaskRevision(expected_revision=2), self.attempt()
        )
        self.assertEqual(task.state, "claimed")
        with self.assertRaises(ConflictError):
            preparation.record_attempt(
                self.session, self.engineer, task.id, TaskRevision(expected_revision=3), self.attempt()
            )
        self.session.rollback()
        self.assertEqual(len(preparation.list_attempts(self.session, self.engineer, task.id)), 1)
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).revision, 3)

    def test_attempt_origins_are_scoped_by_installation_and_visible_in_history(self):
        task = self.claim(self.create())
        for index, installation in enumerate(("dev-one", "dev-two"), start=2):
            attempt = self.attempt().model_copy(
                update={"origin": self.attempt().origin.model_copy(update={"installation_id": installation})}
            )
            task = preparation.record_attempt(
                self.session, self.engineer, task.id, TaskRevision(expected_revision=index), attempt
            )
        self.assertEqual(len(preparation.list_attempts(self.session, self.engineer, task.id)), 2)
        event = preparation.list_events(self.session, self.engineer, task.id)[0]
        self.assertEqual(event.changes["attempt_origin"], {"installation_id": "dev-two", "record_id": "conversion-1"})

    def test_caller_transaction_rollback_keeps_creation_and_history_atomic(self):
        task = preparation.create_task(self.session, self.submitter, TaskCreate(title="Transactional"), commit=False)
        self.session.rollback()
        with self.assertRaises(NotFoundError):
            preparation.get_task(self.session, self.admin, task.id)
        self.assertEqual(self.session.exec(select(PreparationTaskEventEntity)).all(), [])

    def test_repeated_additive_upgrade_preserves_tasks_attempts_and_history(self):
        task = self.claim(self.create())
        task = preparation.record_attempt(
            self.session, self.engineer, task.id, TaskRevision(expected_revision=2), self.attempt()
        )
        create_tables(self.engine)
        create_tables(self.engine)
        self.assertEqual(preparation.get_task(self.session, self.admin, task.id), task)
        self.assertEqual(len(preparation.list_attempts(self.session, self.admin, task.id)), 1)
        self.assertEqual(len(preparation.list_events(self.session, self.admin, task.id)), 3)

    def test_database_rejects_history_and_attempt_edits_or_deletion(self):
        task = self.claim(self.create())
        preparation.record_attempt(
            self.session, self.engineer, task.id, TaskRevision(expected_revision=2), self.attempt()
        )
        for table in ("preparation_task_events", "preparation_attempts"):
            for statement in (f"DELETE FROM {table}", f"UPDATE {table} SET actor_user_id = 999"):
                with self.subTest(statement=statement), self.assertRaises(DBAPIError):
                    self.session.execute(text(statement))
                self.session.rollback()
        self.assertEqual(len(self.session.exec(select(PreparationAttemptEntity)).all()), 1)

    def test_release_waiting_claim_retains_missing_information(self):
        task = self.claim(self.create())
        task = preparation.request_information(
            self.session,
            self.engineer,
            task.id,
            InformationRequest(expected_revision=2, missing_inputs=["test_data"], reason="Need data"),
        )
        task = preparation.release_claim(self.session, self.engineer, task.id, TaskRevision(expected_revision=3))
        self.assertEqual(
            (task.state, task.assignee_user_id, task.missing_inputs), ("awaiting_information", None, ["test_data"])
        )


class PreparationApiTests(PreparationFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(router)
        self.active_claims = self.engineer
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.active_claims
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_queue_detail_claim_conflict_and_history(self):
        task = self.create()
        url = f"/api/preparation-tasks/{task.id}"
        self.assertEqual(self.client.get("/api/preparation-tasks").json()[0]["id"], str(task.id))
        self.assertEqual(self.client.get(url).json()["revision"], 1)
        self.assertEqual(self.client.post(url + "/claim", json={"expected_revision": 1}).status_code, 200)
        self.assertEqual(self.client.post(url + "/release-claim", json={"expected_revision": 1}).status_code, 409)
        self.session.rollback()
        self.assertEqual(len(self.client.get(url + "/events").json()), 2)
        self.assertEqual(self.client.get(url + "/attempts").json(), [])

    def test_http_transition_contracts_round_trip(self):
        task = self.create()
        url = f"/api/preparation-tasks/{task.id}"
        requests = [
            ("claim", {"expected_revision": 1}),
            ("request-information", {"expected_revision": 2, "missing_inputs": ["test_data"], "reason": "Need data"}),
            ("resolve-inputs", {"expected_revision": 3, "resolved_inputs": ["test_data"], "reason": "Captured data"}),
            ("release-claim", {"expected_revision": 4}),
        ]
        for action, payload in requests:
            response = self.client.post(url + "/" + action, json=payload)
            self.assertEqual(response.status_code, 200, response.text)
        self.active_claims = self.admin
        reassigned = self.client.post(
            url + "/reassign", json={"expected_revision": 5, "assignee_user_id": 3, "reason": "Assign next engineer"}
        )
        self.assertEqual(reassigned.status_code, 200)
        self.assertEqual(reassigned.json()["assignee_user_id"], 3)
        cancelled = self.client.post(url + "/cancel", json={"expected_revision": 6, "reason": "Superseded"})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertEqual(len(self.client.get(url + "/events?limit=2&offset=1").json()), 2)

    def test_invalid_requests_and_unknown_task_have_explicit_status(self):
        task = self.create()
        url = f"/api/preparation-tasks/{task.id}"
        for payload in (
            {},
            {"expected_revision": 0},
            {"expected_revision": True},
            {"expected_revision": 1, "assignee_user_id": 3},
        ):
            self.assertEqual(self.client.post(url + "/claim", json=payload).status_code, 422)
        self.assertEqual(self.client.get("/api/preparation-tasks?limit=101").status_code, 422)
        self.assertEqual(self.client.get(url + "/events?offset=-1").status_code, 422)
        self.assertEqual(self.client.get(f"/api/preparation-tasks/{uuid4()}").status_code, 404)
        self.assertEqual(
            self.client.post("/api/preparation-tasks", json={"title": "No handoff endpoint"}).status_code, 405
        )

    def test_unrelated_account_cannot_read_or_mutate_any_task_route(self):
        task = self.create()
        self.active_claims = self.claims(5)
        url = f"/api/preparation-tasks/{task.id}"
        for suffix in ("", "/events", "/attempts"):
            self.assertEqual(self.client.get(url + suffix).status_code, 403)
        requests = {
            "claim": {"expected_revision": 1},
            "release-claim": {"expected_revision": 1},
            "request-information": {"expected_revision": 1, "missing_inputs": ["test_data"], "reason": "Need data"},
            "resolve-inputs": {"expected_revision": 1, "resolved_inputs": ["test_data"], "reason": "Done"},
            "reassign": {"expected_revision": 1, "assignee_user_id": 3, "reason": "Assign"},
            "cancel": {"expected_revision": 1, "reason": "Cancel"},
        }
        for suffix, payload in requests.items():
            self.assertEqual(self.client.post(url + "/" + suffix, json=payload).status_code, 403)
        self.assertEqual(self.session.get(PreparationTaskEntity, task.id).revision, 1)

    def test_schema_rejects_unbounded_or_unsafe_input_names(self):
        for payload in (
            {"title": "  "},
            {"title": "x", "missing_inputs": ["../file"]},
            {"title": "x", "missing_inputs": ["input"] * 51},
            {"title": "x", "role": "admin"},
        ):
            with self.assertRaises(ValidationError):
                TaskCreate(**payload)
