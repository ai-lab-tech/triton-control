"""Validation execution, runner ownership and exact input binding regressions."""

import io
import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.api.preparation_api import router
from app.core.canonical import inventory_digest
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import OperationEntity, UserEntity
from app.exceptions import ForbiddenError, UnprocessableEntityError
from app.repositories.operations import VALIDATION_KIND, validation_attempts
from app.schemas.materialization import StorageBinding
from app.schemas.operations import OperationRequest
from app.schemas.validation import ValidationManifest
from app.services.candidate_validation import execution
from app.services.candidate_validation.workload import Inputs, render
from app.services.operations import OperationService, OperationWorker
from app.services.operations import service as registered_service
from app.services.validation import artifact_subjects, document_digest
from tests.materialization_fixtures import plan, policy
from tests.test_preparation import PreparationFixture

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/validation/v1alpha1/fixtures"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_bytes())


def bound_manifest():
    manifest = ValidationManifest.model_validate(fixture("manifest"))
    expected = plan()
    repo = manifest.repository.model_copy(
        update={
            "materializer_image": expected.materializer_image,
            "inventory_digest": inventory_digest(expected.source_inventory),
            "prepared_inventory_digest": inventory_digest(expected.prepared_inventory),
        }
    )
    manifest = manifest.model_copy(update={"repository": repo})
    inputs = Inputs(
        evaluator=manifest.evaluator,
        claim="verified-inputs",
        plan=expected,
        policy=policy(),
        storage=StorageBinding(bucket="synthetic-models", prefix="toy/r1", secret_name="read-only"),
    )
    return manifest, inputs


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.manifest, _ = bound_manifest()
        self.core = Mock()
        self.job = NS(metadata=NS(uid="job-uid"))
        self.pod = NS(
            metadata=NS(
                name="runner", uid="pod-uid", owner_references=[NS(kind="Job", uid="job-uid", controller=True)]
            ),
            status=NS(
                phase="Succeeded",
                init_container_statuses=[],
                container_statuses=[
                    NS(
                        name="evaluator",
                        restart_count=0,
                        state=NS(
                            terminated=NS(
                                exit_code=0,
                                started_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                                finished_at=datetime.fromisoformat("2026-01-01T00:01:00+00:00"),
                            )
                        ),
                    )
                ],
            ),
            spec=NS(
                containers=[NS(image=self.manifest.evaluator.image)],
                init_containers=[NS(name="triton", image=self.manifest.image)]
                + [
                    NS(name=name, image=self.manifest.repository.materializer_image)
                    for name in ("materialize-models", "verify-inputs", "release-readiness")
                ],
            ),
        )
        self.core.list_namespaced_pod.return_value = NS(items=[self.pod])
        self.core.read_namespaced_pod.return_value = self.pod

    def report(self, outcome):
        report = fixture("report-" + outcome)
        report.update(
            manifestDigest=document_digest(self.manifest),
            subjects=artifact_subjects(self.manifest).model_dump(mode="json", by_alias=True),
        )
        return report

    def read(self, report):
        self.core.read_namespaced_pod_log.return_value = io.BytesIO(json.dumps(report).encode())
        return execution.read_result(self.core, self.job, "isolated", self.manifest)

    def test_supported_failed_and_unsupported_reports_keep_distinct_outcomes(self):
        for outcome in ("passed", "failed", "unsupported", "error"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.read(self.report(outcome)).status, outcome)

    def test_incomplete_metrics_and_different_subject_rejected(self):
        raw = self.report("passed")
        raw["metrics"].pop()
        with self.assertRaises(ValueError):
            self.read(raw)
        raw = self.report("passed")
        raw["manifestDigest"] = "sha256:" + "a" * 64
        with self.assertRaises(ValueError):
            self.read(raw)

    def test_old_report_from_same_subject_is_rejected(self):
        report = self.report("passed")
        report["startedAt"] = "2025-12-31T20:00:00Z"
        with self.assertRaisesRegex(ValueError, "report-outside-runner-lifetime"):
            self.read(report)

    def test_foreign_owner_restarted_candidate_and_replaced_pod_rejected(self):
        self.pod.metadata.owner_references[0].uid = "another-job"
        with self.assertRaises(ValueError):
            self.read(self.report("passed"))
        self.core.read_namespaced_pod_log.assert_not_called()
        self.pod.metadata.owner_references[0].uid = self.job.metadata.uid
        self.pod.status.init_container_statuses.append(NS(restart_count=1))
        with self.assertRaises(ValueError):
            self.read(self.report("passed"))
        self.pod.status.init_container_statuses.clear()
        self.core.read_namespaced_pod.return_value = NS(metadata=NS(uid="replacement"))
        with self.assertRaises(ValueError):
            self.read(self.report("passed"))

    def test_shared_readiness_and_digest_scoped_mounts(self):
        manifest, inputs = bound_manifest()
        config, job = render(manifest, inputs, "candidate", "isolated")
        spec = job["spec"]["template"]["spec"]
        init, verify, server, ready = spec["initContainers"]
        self.assertEqual(
            [init["name"], verify["name"], server["name"], ready["name"]],
            ["materialize-models", "verify-inputs", "triton", "release-readiness"],
        )
        self.assertEqual(ready["startupProbe"]["exec"], ready["readinessProbe"]["exec"])
        self.assertTrue(config["immutable"])
        self.assertFalse(spec["automountServiceAccountToken"])
        runner = spec["containers"][0]
        self.assertEqual(runner["image"], manifest.evaluator.image)
        self.assertTrue(all(mount["readOnly"] for mount in runner["volumeMounts"]))
        self.assertNotIn("secret", json.dumps(runner))
        changed = manifest.evaluator.model_copy(update={"version": "another"})
        with self.assertRaises(ValueError):
            render(manifest.model_copy(update={"evaluator": changed}), inputs, "candidate", "isolated")

    def test_lost_create_response_recovers_only_the_suspended_job_and_retains_uid(self):
        manifest, inputs = bound_manifest()
        row = OperationEntity(
            id=uuid4().hex,
            target=str(uuid4()),
            result={"namespace": "isolated"},
            payload=manifest.model_dump(mode="json", by_alias=True),
        )
        core, batch, ctx = Mock(), Mock(), Mock()
        core.read_namespaced_config_map.return_value = NS(
            immutable=True, metadata=NS(uid="binding"), data={"inputs.json": inputs.model_dump_json()}
        )
        batch.read_namespaced_job.side_effect = ApiException(status=404)
        row.result = execution.reconcile(row, ctx, core, batch).result
        batch.create_namespaced_job.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError):
            execution.reconcile(row, ctx, core, batch)
        body = batch.create_namespaced_job.call_args.args[1]
        batch.read_namespaced_job.side_effect = None
        job = NS(metadata=NS(uid="owned", annotations=body["metadata"]["annotations"]), spec=NS(suspend=True))
        batch.read_namespaced_job.return_value = job
        row.result = execution.reconcile(row, ctx, core, batch).result
        self.assertEqual(row.result["jobUID"], "owned")
        batch.create_namespaced_job.assert_called_once()
        job.metadata.uid = "replaced"
        self.assertEqual(execution.reconcile(row, ctx, core, batch).state, "failed")
        batch.delete_namespaced_job.assert_not_called()


    def test_completed_job_with_missing_evaluator_logs_cannot_publish_a_report(self):
        # Arrange
        row = OperationEntity(
            id=uuid4().hex, target=str(uuid4()), payload=self.manifest.model_dump(mode="json", by_alias=True),
            result={"namespace": "isolated", "jobUID": self.job.metadata.uid},
        )
        self.job.metadata.annotations = {execution.OWNER: row.id}
        self.job.spec = NS(suspend=False)
        self.job.status = NS(conditions=[NS(type="Complete", status="True")])
        batch = Mock()
        batch.read_namespaced_job.return_value = self.job
        self.core.read_namespaced_pod_log.side_effect = ApiException(status=404)

        # Act
        with self.assertRaisesRegex(execution.MetadataError, "completed-runner-evidence-missing"):
            execution.reconcile(row, Mock(), self.core, batch)

        # Assert
        batch.delete_namespaced_job.assert_not_called()

    def test_cleanup_observes_completion_when_the_owned_job_disappears_before_delete(self):
        # Arrange
        row = OperationEntity(
            id=uuid4().hex, target=str(uuid4()), payload=self.manifest.model_dump(mode="json", by_alias=True),
            result={"namespace": "isolated", "jobUID": "owned", "finishedState": "succeeded", "outcome": "passed"},
        )
        batch = Mock()
        batch.read_namespaced_job.return_value = NS(metadata=NS(uid="owned", annotations={execution.OWNER: row.id}))
        batch.delete_namespaced_job.side_effect = ApiException(status=404)

        # Act
        result = execution.reconcile(row, Mock(), self.core, batch)

        # Assert
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.result, row.result)
        self.assertEqual(batch.delete_namespaced_job.call_args.kwargs["body"]["preconditions"], {"uid": "owned"})


class ValidationAccessTests(PreparationFixture, unittest.TestCase):
    def test_submission_rejects_the_application_namespace_before_reserving_work(self):
        # Arrange
        task = self.claim(self.create())
        service = OperationService()
        service.register(VALIDATION_KIND, execution.ValidationAdapter())
        request = OperationRequest(kind=VALIDATION_KIND, target=str(task.id), payload=fixture("manifest"))

        # Act
        with patch.dict(os.environ, POD_NAMESPACE="control", TRITON_VALIDATION_NAMESPACE="control"):
            with self.assertRaises(UnprocessableEntityError):
                service.submit(self.session, self.engineer, request, "unsafe-namespace")

        # Assert
        self.assertEqual(validation_attempts(self.session, task.id, 10, 0), [])

    def test_only_assignee_can_submit_and_failed_attempt_cannot_be_overwritten(self):
        if self.engine.dialect.name == "sqlite":
            self.enterContext(patch("app.repositories.operations.now", lambda _: datetime.utcnow()))
        task = self.claim(self.create())
        service = OperationService()
        service.register(VALIDATION_KIND, execution.ValidationAdapter())
        request = OperationRequest(kind=VALIDATION_KIND, target=str(task.id), payload=fixture("manifest"))
        with patch.dict(os.environ, TRITON_VALIDATION_NAMESPACE="isolated"):
            with self.assertRaises(ForbiddenError):
                service.submit(self.session, self.claims(3), request, "validation")
            accepted = service.submit(self.session, self.engineer, request, "validation")
            row = self.session.get(OperationEntity, accepted.id)
            row.state, row.result = "failed", {"outcome": "failed"}
            self.session.commit()
            with self.assertRaises(UnprocessableEntityError):
                service.retry(self.session, self.engineer, row.id)
            self.assertEqual(service.read(self.session, self.engineer, accepted.id).result, {"outcome": "failed"})
            another = service.submit(self.session, self.engineer, request, "validation-2")
            self.assertNotEqual(another.id, accepted.id)

    def test_task_api_returns_status_url_and_authorized_history(self):
        if self.engine.dialect.name == "sqlite":
            self.enterContext(patch("app.repositories.operations.now", lambda _: datetime.utcnow()))
        task = self.claim(self.create())
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = lambda: self.session
        app.dependency_overrides[get_claims] = lambda: self.engineer
        url = f"/api/preparation-tasks/{task.id}/validations"
        self.enterContext(patch.dict(registered_service.adapters, {VALIDATION_KIND: execution.ValidationAdapter()}))
        self.enterContext(patch.dict(os.environ, TRITON_VALIDATION_NAMESPACE="isolated"))
        with TestClient(app) as http:
            response = http.post(url, json=fixture("manifest"), headers={"Idempotency-Key": "new-run"})
            self.assertEqual(response.status_code, 202, response.text)
            self.assertEqual(response.headers["Location"], response.json()["status_url"])
            self.assertEqual(http.get(url).json(), [response.json()])
            app.dependency_overrides[get_claims] = lambda: self.claims(3)
            self.assertEqual(http.get(url).status_code, 403)

    def test_admin_cancellation_completes_after_submitter_deactivation(self):
        if self.engine.dialect.name == "sqlite":
            self.enterContext(patch("app.repositories.operations.now", lambda _: datetime.utcnow()))
        self.enterContext(patch.dict(os.environ, TRITON_VALIDATION_NAMESPACE="isolated"))
        service = OperationService()
        service.register(VALIDATION_KIND, execution.ValidationAdapter())
        task = self.claim(self.create())
        request = OperationRequest(kind=VALIDATION_KIND, target=str(task.id), payload=fixture("manifest"))
        run = service.submit(self.session, self.engineer, request, "cancelled-before-launch")
        self.session.get(UserEntity, 2).is_active = False
        self.session.commit()
        service.cancel(self.session, self.admin, run.id)
        with patch.object(execution, "api_client", side_effect=AssertionError("nothing to remove")):
            self.assertTrue(OperationWorker(service, lambda: Session(self.engine)).run_once())
        self.assertEqual(service.read(self.session, self.admin, run.id).state, "cancelled")
