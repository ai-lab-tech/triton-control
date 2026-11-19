"""Engineering read-model authorization and API conflict recovery."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.preparation_api import router
from app.core.security import get_claims
from app.core import installation_policy
from app.db.database import get_session
from app.db.entities import CandidateImportEntity, ServingCandidateEntity
from app.exceptions import ForbiddenError
from app.services import engineering_queue, preparation
from tests.test_preparation import PreparationFixture


class EngineeringQueueTests(PreparationFixture, unittest.TestCase):
    def client(self, claims):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: claims
        return self.enterContext(TestClient(app))

    def test_queue_exposes_current_names_and_incomplete_task_claim(self):
        task = self.create(["test_data"])
        response = self.client(self.engineer).get("/api/preparation-tasks/queue")
        self.assertEqual(response.status_code, 200)
        row = response.json()[0]
        self.assertEqual(row["task"]["id"], str(task.id))
        self.assertEqual(row["submitter_name"], "User 4")
        self.assertEqual(row["team_name"], self.team.name)
        self.assertTrue(row["actions"]["claim"])
        self.assertFalse(row["actions"]["open_workspace"])
        self.assertEqual(row["task"]["missing_inputs"], ["test_data"])

    def test_submitter_visibility_does_not_grant_engineer_actions(self):
        task = self.create()
        result = engineering_queue.overview(self.session, self.submitter, task.id)
        self.assertFalse(result.actions.claim)
        self.assertFalse(result.actions.request_information)
        self.assertFalse(result.actions.open_workspace)
        with self.assertRaises(ForbiddenError):
            engineering_queue.overview(self.session, self.claims(5), task.id)
        self.assertEqual(engineering_queue.queue(self.session, self.claims(5)), [])

    def test_stale_claim_returns_conflict_and_overview_shows_winner(self):
        task = self.create(["test_data"])
        client = self.client(self.engineer)
        url = f"/api/preparation-tasks/{task.id}"
        self.claim(task)
        response = client.post(url + "/claim", json={"expected_revision": task.revision})
        self.assertEqual(response.status_code, 409)
        current = client.get(url + "/overview").json()
        self.assertEqual(current["assignee_name"], "User 2")
        self.assertFalse(current["actions"]["claim"])
        self.assertTrue(current["actions"]["release"])
        self.assertEqual(current["task"]["missing_inputs"], ["test_data"])

    def test_retains_every_candidate_without_changing_task_state(self):
        task = self.create()
        for index in range(2):
            record_id = f"candidate-{index}"
            self.session.add(ServingCandidateEntity(installation_id="fixture", record_id=record_id,
                                                   digest="sha256:" + "a" * 64, payload="{}", inventories="{}"))
            self.session.flush()
            self.session.add(CandidateImportEntity(installation_id="fixture", record_id=record_id,
                candidate_digest="sha256:" + "a" * 64, import_digest="sha256:" + "b" * 64,
                owner_user_id=2, task_id=task.id))
        self.session.commit()
        result = engineering_queue.overview(self.session, self.engineer, task.id)
        self.assertEqual([item.record_id for item in result.candidates], ["candidate-0", "candidate-1"])
        self.assertEqual(result.task.state, "queued")
        self.assertEqual(preparation.get_task(self.session, self.engineer, task.id).revision, task.revision)

    def test_production_rejects_queue_and_detail(self):
        task = self.create()
        client = self.client(self.engineer)
        with patch.object(installation_policy, "mode", return_value="production"):
            for path in ("/api/preparation-tasks/queue", f"/api/preparation-tasks/{task.id}/overview"):
                self.assertEqual(client.get(path).status_code, 403)


from tests.test_experiment_snapshots import SnapshotFixture


class SnapshotQueueTests(SnapshotFixture, unittest.TestCase):
    def test_captured_experiment_is_linked_without_modifying_the_source(self):
        receipt = self.finish()
        original = self.source.data
        result = engineering_queue.overview(self.session, self.engineer, receipt.task.id)
        self.assertEqual(result.source.snapshot_id, receipt.id)
        self.assertEqual(result.source.instance_id, receipt.manifest.instance_id)
        self.assertEqual(result.source.models, [model.name for model in receipt.manifest.request.models])
        self.assertTrue(result.task.missing_inputs)
        self.assertTrue(result.actions.claim)
        self.assertFalse(result.actions.open_workspace)
        from app.schemas.preparation import TaskRevision
        claimed = preparation.claim(self.session, self.engineer, receipt.task.id,
                                    TaskRevision(expected_revision=receipt.task.revision))
        current = engineering_queue.overview(self.session, self.engineer, claimed.id)
        self.assertTrue(current.actions.open_workspace)
        self.assertEqual(current.task.missing_inputs, result.task.missing_inputs)
        self.assertEqual(self.source.data, original)
