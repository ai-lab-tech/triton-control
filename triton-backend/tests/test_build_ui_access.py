"""AAA coverage for task UI and narrowly scoped workspace build access."""

import os
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlmodel import Session

from app.db.entities import PreparationTaskEntity
from app.exceptions import ForbiddenError, UnauthorizedError
from app.repositories import operations, teams
from app.services.builds import access, contexts, execution
from tests import test_builds as fixtures


class BuildUiTests(fixtures.BuildTests):
    def test_workspace_context_cannot_be_used_for_another_task(self):
        # Arrange
        with Session(self.engine) as session:
            op = operations.get(session, self.request.context_id)
            op.payload = {**op.payload, "request": {**op.payload["request"], "task_id": str(uuid4())}}
            session.add(op)
            session.commit()
            # Act / Assert
            with self.assertRaisesRegex(ForbiddenError, "different task"):
                execution.submit(session, self.claims, self.request, "wrong-task")

    def test_scoped_workspace_cannot_read_unbound_context(self):
        # Arrange
        scoped = {**self.claims, "build_task_id": str(self.task_id)}
        with Session(self.engine) as session:
            # Act / Assert
            with self.assertRaisesRegex(ForbiddenError, "restricted"):
                contexts.read(session, scoped, self.request.context_id)

    def test_wrong_task_cannot_read_cancel_retry_or_list_build(self):
        # Arrange
        build = self.submit_build()
        scoped = {**self.claims, "build_task_id": str(uuid4())}
        with Session(self.engine) as session:
            # Act / Assert
            for action in (execution.read, execution.cancel, execution.retry):
                with self.subTest(action=action.__name__), self.assertRaises(ForbiddenError):
                    action(session, scoped, build.id)
            with self.assertRaises(ForbiddenError):
                execution.list_for_task(session, scoped, self.task_id)

    def test_task_list_excludes_other_tasks(self):
        # Arrange
        build = self.submit_build()
        other = uuid4()
        with Session(self.engine) as session:
            session.add(PreparationTaskEntity(id=other, title="Other",
                assigned_team_id=teams.INSTALLATION_TEAM_ID, submitter_user_id=1))
            session.commit()
            # Act
            current = execution.list_for_task(session, self.claims, self.task_id)
            unrelated = execution.list_for_task(session, self.claims, other)
            # Assert
            self.assertEqual([row.id for row in current], [build.id])
            self.assertEqual(unrelated, [])

    def test_options_expose_only_repositories_and_limit(self):
        # Arrange
        with Session(self.engine) as session:
            # Act
            result = execution.options(session, self.claims, self.task_id)
            # Assert
            self.assertEqual(result.repositories, self.policy.allowed_repositories)
            self.assertEqual(set(result.model_dump()), {"task_id", "repositories", "max_context_bytes", "staging_profiles", "local_archive"})

    def test_ui_retry_keeps_failed_attempt_and_creates_distinct_attempt(self):
        # Arrange
        build = self.submit_build()
        self.run_build(build.id)
        self.finish(build.id, success=False)
        with Session(self.engine) as session:
            # Act
            execution.retry(session, self.claims, build.id)
        retried = self.run_build(build.id)
        # Assert
        self.assertEqual([row.number for row in retried.attempts], [1, 2])
        self.assertEqual(retried.attempts[0].state, "failed")
        self.assertIsNone(retried.attempts[0].final_digest)
        self.assertNotEqual(retried.attempts[0].destination, retried.attempts[1].destination)

    def test_production_blocks_build_ui_before_providers(self):
        # Arrange
        build = self.submit_build()
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}), Session(self.engine) as session:
            # Act / Assert
            for action in (execution.read, execution.cancel, execution.retry):
                with self.subTest(action=action.__name__), self.assertRaises(ForbiddenError):
                    action(session, self.claims, build.id)
            with self.assertRaises(ForbiddenError):
                execution.options(session, self.claims, self.task_id)
            with self.assertRaises(ForbiddenError):
                execution.list_for_task(session, self.claims, self.task_id)

    def test_expired_workspace_credential_never_becomes_user_claims(self):
        # Arrange
        with Session(self.engine) as session, patch.object(access, "authenticate_workspace",
                side_effect=UnauthorizedError("Workspace authentication invalid")), \
                patch.object(access.users, "find_by_id") as resolve:
            # Act / Assert
            with self.assertRaises(UnauthorizedError):
                access.authenticate(session, "development/code-one", "expired")
            resolve.assert_not_called()

    def test_personal_workspace_cannot_use_build_authentication(self):
        # Arrange
        with Session(self.engine) as session, patch.object(access, "authenticate_workspace",
                return_value=SimpleNamespace(id=42, owner_user_id=1)), \
                patch.object(access.task_workspaces, "for_workspace", return_value=None):
            # Act / Assert
            with self.assertRaisesRegex(ForbiddenError, "assigned task"):
                access.authenticate(session, "development/code-one", "fixture")

    def test_unbound_context_normalization_preserves_legacy_idempotency(self):
        # Arrange
        with Session(self.engine) as session:
            op = operations.get(session, self.request.context_id)
            payload = dict(op.payload["request"])
            payload.pop("task_id", None)
            # Act
            normalized = contexts.adapter.normalize(session, payload)
        # Assert
        self.assertNotIn("task_id", normalized["request"])

    def test_progress_requires_owned_executor_observation(self):
        # Arrange
        build = self.submit_build()
        self.run_build(build.id)
        # Act
        queued = self.run_build(build.id)
        self.provider.observation = fixtures.jobs_kubernetes.Observation(running=True)
        building = self.run_build(build.id)
        self.provider.observation = fixtures.jobs_kubernetes.Observation(running=True, logs="Pushing image to registry")
        pushing = self.run_build(build.id)
        # Assert
        self.assertEqual(queued.operation.result["phase"], "queued")
        self.assertEqual(building.operation.result["phase"], "building")
        self.assertEqual(pushing.operation.result["phase"], "pushing")
        self.assertIsNone(pushing.attempts[0].final_digest)
