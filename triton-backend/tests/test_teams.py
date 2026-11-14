"""Arrange–Act–Assert tests for team policy, persistence and HTTP contracts."""

import unittest
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from app.api.team_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import CodeServerEntity, TeamEventEntity, UserEntity, UserTeamEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.repositories import teams as repository
from app.repositories import users
from app.schemas.teams import TeamRequest
from app.services import teams
from app.services.access import ensure_instance_access
from app.services.development.workspaces import _get_owned


class TeamSchemaTests(unittest.TestCase):
    def test_name_normalization_produces_one_canonical_identity(self):
        # Arrange
        name = "  ML-Engineering  "
        # Act
        request = TeamRequest(name=name)
        # Assert
        self.assertEqual(request.name, "ml-engineering")

    def test_invalid_names_and_extra_permission_fields_are_rejected(self):
        # Arrange
        requests = [{"name": name} for name in ["", "  ", "../team", "a" * 65, "two teams"]]
        requests.append({"name": "team", "role": "admin"})
        # Act
        for request in requests:
            with self.subTest(request=request), self.assertRaises(ValidationError) as raised:
                TeamRequest(**request)
            # Assert
            self.assertTrue(raised.exception.errors())

    def test_non_admin_is_denied_before_any_database_access(self):
        # Arrange
        session = MagicMock()
        # Act
        with self.assertRaises(ForbiddenError):
            teams.list_teams(session, {"role": "member"})
        # Assert
        self.assertEqual(session.mock_calls, [])


class TeamFixture:
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        event.listen(self.engine, "connect", lambda connection, _: connection.execute("PRAGMA foreign_keys=ON"))
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.session.add_all(
            [
                UserEntity(id=1, name="Admin", email="admin@test.invalid", role="admin", password_hash="hash"),
                UserEntity(id=2, name="Engineer", email="engineer@test.invalid", role="member", password_hash="hash"),
                UserEntity(id=3, name="Viewer", email="viewer@test.invalid", role="viewer", password_hash="hash"),
                UserEntity(id=4, name="Inactive", email="inactive@test.invalid", role="member", is_active=False),
            ]
        )
        self.session.commit()
        self.claims = {"role": "admin", "email": "admin@test.invalid"}

    def create(self, name="engineering"):
        return teams.create_team(self.session, self.claims, TeamRequest(name=name))

class TeamTests(TeamFixture, unittest.TestCase):
    def test_create_and_repeat_migration_preserve_teams_memberships_and_users(self):
        # Arrange
        team = self.create()
        teams.set_member(self.session, self.claims, team.id, 2, active=True)
        # Act
        create_tables(self.engine)
        create_tables(self.engine)
        # Assert
        self.assertEqual(teams.list_teams(self.session, self.claims)[0].id, team.id)
        self.assertEqual(repository.eligible_user_ids(self.session, team.id), [2])
        self.assertEqual(len(self.session.exec(select(UserEntity)).all()), 4)

    def test_duplicate_team_name_is_a_conflict_without_partial_history(self):
        # Arrange
        original = self.create()
        # Act
        with self.assertRaises(ConflictError):
            self.create(" ENGINEERING ")
        # Assert
        self.assertEqual(len(repository.list_all(self.session)), 1)
        self.assertEqual(len(repository.list_events(self.session, original.id, 100)), 1)

    def test_conflicting_rename_rolls_back_team_and_audit(self):
        # Arrange
        first, second = self.create(), self.create("other")
        # Act
        with self.assertRaises(ConflictError):
            teams.update_team(self.session, self.claims, second.id, TeamRequest(name=first.name))
        # Assert
        self.assertEqual(repository.get(self.session, second.id).name, "other")
        self.assertEqual(len(repository.list_events(self.session, second.id, 100)), 1)

    def test_members_can_join_multiple_teams_without_changing_access(self):
        # Arrange
        first, second = self.create(), self.create("platform")
        member = users.find_by_id(self.session, 2)
        claims = {"role": "member", "email": member.email}
        self.session.add(
            CodeServerEntity(
                owner_user_id=1,
                name="private",
                namespace="test",
                statefulset_name="private",
                service_name="private",
                secret_name="private",
                image="image",
            )
        )
        self.session.commit()
        # Act
        teams.set_member(self.session, self.claims, first.id, 2, active=True)
        teams.set_member(self.session, self.claims, second.id, 2, active=True)
        # Assert
        self.assertEqual(repository.eligible_user_ids(self.session, first.id), [2])
        self.assertEqual(repository.eligible_user_ids(self.session, second.id), [2])
        self.assertEqual(member.role, "member")
        self.assertEqual(member.assigned_instances, [])
        with self.assertRaises(ForbiddenError):
            ensure_instance_access(self.session, claims, "private-instance")
        with self.assertRaises(ForbiddenError):
            _get_owned(self.session, claims, 1)

    def test_repeated_membership_requests_are_idempotent_and_revocation_retains_history(self):
        # Arrange
        team = self.create()
        # Act
        for _ in range(2):
            teams.set_member(self.session, self.claims, team.id, 2, active=True)
        for _ in range(2):
            teams.set_member(self.session, self.claims, team.id, 2, active=False)
        # Assert
        self.assertEqual(repository.eligible_user_ids(self.session, team.id), [])
        memberships = teams.list_members(self.session, self.claims, team.id)
        self.assertEqual(len(memberships), 1)
        self.assertFalse(memberships[0].is_active)
        self.assertEqual(
            [e.action for e in teams.list_history(self.session, self.claims, team.id)],
            ["member_removed", "member_added", "team_created"],
        )

    def test_deactivated_team_suspends_eligibility_and_keeps_memberships(self):
        # Arrange
        team = self.create()
        teams.set_member(self.session, self.claims, team.id, 2, active=True)
        # Act
        teams.update_team(self.session, self.claims, team.id, TeamRequest(name=team.name, is_active=False))
        # Assert
        self.assertEqual(repository.eligible_user_ids(self.session, team.id), [])
        self.assertTrue(repository.membership(self.session, team.id, 2).is_active)
        with self.assertRaises(ConflictError):
            teams.set_member(self.session, self.claims, team.id, 3, active=True)

    def test_reactivation_does_not_restore_explicitly_revoked_membership(self):
        # Arrange
        team = self.create()
        teams.set_member(self.session, self.claims, team.id, 2, active=True)
        teams.update_team(self.session, self.claims, team.id, TeamRequest(name=team.name, is_active=False))
        teams.set_member(self.session, self.claims, team.id, 2, active=False)
        # Act
        teams.update_team(self.session, self.claims, team.id, TeamRequest(name=team.name))
        # Assert
        self.assertEqual(repository.eligible_user_ids(self.session, team.id), [])
        self.assertFalse(teams.list_members(self.session, self.claims, team.id)[0].eligible)

    def test_viewer_membership_never_grants_engineering_eligibility(self):
        # Arrange
        team = self.create()
        # Act
        result = teams.set_member(self.session, self.claims, team.id, 3, active=True)
        # Assert
        self.assertFalse(result[0].eligible)
        self.assertEqual(result[0].role, "viewer")
        self.assertEqual(repository.eligible_user_ids(self.session, team.id), [])

    def test_current_user_status_and_role_are_rechecked_for_routing(self):
        # Arrange
        team = self.create()
        teams.set_member(self.session, self.claims, team.id, 2, active=True)
        user = users.find_by_id(self.session, 2)
        # Act
        user.is_active = False
        self.session.commit()
        inactive = repository.eligible_user_ids(self.session, team.id)
        user.is_active, user.role = True, "viewer"
        self.session.commit()
        demoted = repository.eligible_user_ids(self.session, team.id)
        # Assert
        self.assertEqual(inactive, [])
        self.assertEqual(demoted, [])

    def test_inactive_or_missing_users_cannot_be_added(self):
        # Arrange
        team = self.create()
        # Act
        for user_id, error in [(4, ConflictError), (999, NotFoundError)]:
            with self.subTest(user_id=user_id), self.assertRaises(error):
                teams.set_member(self.session, self.claims, team.id, user_id, active=True)
        # Assert
        self.assertEqual(teams.list_members(self.session, self.claims, team.id), [])
        self.assertEqual(len(teams.list_history(self.session, self.claims, team.id)), 1)

    def test_deleted_user_membership_does_not_block_deletion_or_erase_audit(self):
        # Arrange
        team = self.create()
        teams.set_member(self.session, self.claims, team.id, 2, active=True)
        # Act
        users.delete(self.session, users.find_by_id(self.session, 2))
        # Assert
        self.assertEqual(teams.list_members(self.session, self.claims, team.id), [])
        events = teams.list_history(self.session, self.claims, team.id)
        self.assertEqual(events[0].subject_user_id, 2)
        self.assertEqual(events[0].action, "member_added")

    def test_legacy_admin_role_can_manage_teams_without_changing_role(self):
        # Arrange
        actor = self.session.get(UserEntity, 1)
        actor.role = "Admin"
        self.session.commit()
        claims = {**self.claims, "role": "Admin"}

        # Act
        team = teams.create_team(self.session, claims, TeamRequest(name="legacy-admin"))
        result = teams.list_teams(self.session, claims)

        # Assert
        self.assertIn(team.id, [item.id for item in result])
        self.assertEqual(actor.role, "Admin")

    def test_eligibility_normalizes_legacy_roles_without_granting_viewer_access(self):
        # Arrange
        team = teams.create_team(self.session, self.claims, TeamRequest(name="legacy-roles"))
        for user_id, role in [(1, "Admin"), (2, " Member "), (3, "Viewer")]:
            user = self.session.get(UserEntity, user_id)
            user.role = role
            self.session.commit()
            teams.set_member(self.session, self.claims, team.id, user_id, active=True)

        # Act
        eligible = repository.eligible_user_ids(self.session, team.id)

        # Assert
        self.assertEqual(eligible, [1, 2])
        self.assertEqual(self.session.get(UserEntity, 3).role, "Viewer")

    def test_installation_group_imports_active_members_once_and_preserves_history(self):
        # Arrange
        legacy = teams.create_team(self.session, self.claims, TeamRequest(name="legacy"))
        teams.set_member(self.session, self.claims, legacy.id, 2, active=True)
        teams.set_member(self.session, self.claims, legacy.id, 3, active=True)
        teams.set_member(self.session, self.claims, legacy.id, 3, active=False)
        history = teams.list_history(self.session, self.claims, legacy.id)

        # Act
        group = teams.initialize_engineering_group(self.session, self.claims)
        imported = teams.list_members(self.session, self.claims, group.id)
        teams.set_member(self.session, self.claims, group.id, 2, active=False)
        repeated = teams.initialize_engineering_group(self.session, self.claims)

        # Assert
        self.assertEqual([member.user_id for member in imported], [2])
        self.assertEqual(group.id, repeated.id)
        self.assertFalse(teams.list_members(self.session, self.claims, group.id)[0].is_active)
        self.assertEqual(teams.list_history(self.session, self.claims, legacy.id), history)
        self.assertEqual(self.session.get(UserEntity, 2).role, "member")

    def test_installation_group_does_not_import_paused_teams_and_cannot_be_paused(self):
        # Arrange
        legacy = teams.create_team(self.session, self.claims, TeamRequest(name="paused"))
        teams.set_member(self.session, self.claims, legacy.id, 2, active=True)
        teams.update_team(self.session, self.claims, legacy.id, TeamRequest(name="paused", is_active=False))

        # Act
        group = teams.initialize_engineering_group(self.session, self.claims)

        # Assert
        self.assertEqual(teams.list_members(self.session, self.claims, group.id), [])
        with self.assertRaises(ConflictError):
            teams.update_team(self.session, self.claims, group.id, TeamRequest(name=group.name, is_active=False))
        with self.assertRaises(ForbiddenError):
            teams.initialize_engineering_group(self.session, {"role": "member"})

    def test_stale_admin_claims_do_not_bypass_current_account_role(self):
        # Arrange
        actor = users.find_by_id(self.session, 1)
        actor.role = "member"
        self.session.commit()
        # Act
        with self.assertRaises(ForbiddenError):
            self.create()
        # Assert
        self.assertEqual(repository.list_all(self.session), [])

    def test_audit_is_bounded_and_retains_before_after_configuration(self):
        # Arrange
        team = self.create()
        # Act
        teams.update_team(self.session, self.claims, team.id, TeamRequest(name="renamed", is_active=False))
        history = teams.list_history(self.session, self.claims, team.id, 1)
        # Assert
        self.assertEqual(len(history), 1)
        self.assertEqual(
            history[0].changes,
            {
                "before": {"name": "engineering", "is_active": True},
                "after": {"name": "renamed", "is_active": False},
            },
        )

    def test_unknown_team_does_not_create_membership_or_event(self):
        # Arrange
        team_id = uuid4()
        # Act
        with self.assertRaises(NotFoundError):
            teams.set_member(self.session, self.claims, team_id, 2, active=True)
        # Assert
        self.assertEqual(self.session.exec(select(UserTeamEntity)).all(), [])
        self.assertEqual(self.session.exec(select(TeamEventEntity)).all(), [])


class TeamApiTests(TeamFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.claims
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_installation_endpoint_is_repeatable(self):
        # Arrange
        url = "/api/teams/engineering"
        # Act
        first = self.client.put(url)
        second = self.client.put(url)
        # Assert
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(len(repository.list_all(self.session)), 1)

    def test_http_contract_normalizes_creates_updates_and_revokes(self):
        # Arrange
        request = {"name": " ML-Engineering "}
        # Act
        created = self.client.post("/api/teams", json=request)
        team_id = created.json()["id"]
        added = self.client.put(f"/api/teams/{team_id}/members/2")
        removed = self.client.delete(f"/api/teams/{team_id}/members/2")
        # Assert
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "ml-engineering")
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json()[0]["eligible"])
        self.assertFalse(removed.json()[0]["is_active"])

    def test_all_routes_deny_members_and_viewers(self):
        # Arrange
        team = self.create()
        requests = [
            ("GET", "/api/teams", None),
            ("PUT", "/api/teams/engineering", None),
            ("POST", "/api/teams", {"name": "new"}),
            ("PUT", f"/api/teams/{team.id}", {"name": "new"}),
            ("GET", f"/api/teams/{team.id}/members", None),
            ("PUT", f"/api/teams/{team.id}/members/2", None),
            ("DELETE", f"/api/teams/{team.id}/members/2", None),
            ("GET", f"/api/teams/{team.id}/events", None),
        ]
        # Act
        for role in ["member", "viewer"]:
            self.claims = {"role": role, "email": "engineer@test.invalid"}
            responses = [self.client.request(method, url, json=body) for method, url, body in requests]
            # Assert
            self.assertEqual([r.status_code for r in responses], [403] * len(requests))
        self.assertEqual(len(repository.list_all(self.session)), 1)

    def test_bad_input_and_conflicts_have_explicit_http_status(self):
        # Arrange
        team = self.create()
        # Act
        conflict = self.client.post("/api/teams", json={"name": team.name})
        invalid = self.client.post("/api/teams", json={"name": "bad/name"})
        missing = self.client.get(f"/api/teams/{uuid4()}/members")
        unbounded = self.client.get(f"/api/teams/{team.id}/events?limit=10000")
        # Assert
        self.assertEqual(
            [conflict.status_code, invalid.status_code, missing.status_code, unbounded.status_code],
            [409, 422, 404, 422],
        )
