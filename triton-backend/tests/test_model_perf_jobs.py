"""Lifecycle, isolation and recovery tests using a real relational database."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import ModelPerfJobEntity, PerfAnalyzerRunEntity, TritonInstanceEntity
from app.exceptions import ConflictError, ForbiddenError
from app.main import app
from app.repositories import perf_jobs as repo
from app.schemas.perf_jobs import StartModelPerfRequest
from app.services.deployment import deployment
from app.services.perf_analyzer import jobs
from app.services.perf_analyzer.jobs_kubernetes import RUN_LABEL, Jobs, manifest


def make_run(model="model-a", version="1", instance_id=1):
    uid = uuid4().hex
    return ModelPerfJobEntity(
        id=uid, instance_id=instance_id, model_name=model, model_version=version,
        image="sdk:test", namespace="test", job_name=f"model-perf-{uid}",
        command=["perf_analyzer", "-m", model], prepared=True,
    )


def fake_job(run, *, suspend=False, conditions=None):
    return NS(metadata=NS(uid=run.id, deletion_timestamp=None, resource_version="1", labels={RUN_LABEL: run.id}),
              spec=NS(suspend=suspend), status=NS(conditions=conditions or []))


def fake_pod(phase="Running", exit_code=None):
    return NS(metadata=NS(name="pod", uid="pod-id"), status=NS(
        phase=phase, conditions=[], container_statuses=[NS(state=NS(
            waiting=None, terminated=NS(exit_code=exit_code) if exit_code is not None else None,
        ))],
    ))


class PerfLifecycleTests(unittest.TestCase):
    def test_reading_deployment_logs_does_not_stop_benchmarks(self):
        with patch.object(deployment, "get_instance_or_404", return_value=NS(
            is_self_deployed=False, deployment_namespace="", deployment_log="logs",
        )), patch.object(deployment, "prepare_instance_deletion") as cleanup:
            self.assertEqual(deployment.get_deployment_logs(self.session, {}, 1), "logs")
            cleanup.assert_not_called()

    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.session.add(TritonInstanceEntity(id=1, name="test", url="http://triton"))
        self.session.commit()
        self.api = MagicMock(spec=Jobs)
        self.api.read.return_value = None
        self.api.pods.return_value = []
        self.api.logs.return_value = ""
        self.api.cleanup.return_value = True
        self.patcher = patch.object(jobs, "Jobs", return_value=self.api)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.access = patch.object(jobs, "get_instance_or_404", return_value=self.session.get(TritonInstanceEntity, 1))
        self.access.start()
        self.addCleanup(self.access.stop)
        self.config = patch.object(jobs.commands, "_fetch_triton_model_config", return_value={"backend": "onnxruntime"})
        self.config.start()
        self.addCleanup(self.config.stop)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.session.close()
        self.engine.dispose()

    def reserve(self, **kwargs):
        run = make_run(**kwargs)
        repo.reserve(self.session, run)
        return run

    def test_atomic_model_slot_spans_versions_and_allows_other_models(self):
        run = self.reserve()
        with self.assertRaisesRegex(ConflictError, run.id):
            self.reserve(version="2")
        self.reserve(model="model-b")
        # Database constraint is a second line of enforcement independent of repository checks.
        self.session.add(make_run(version="2"))
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()
        run.state = "failed"
        self.session.add(run)
        self.session.commit()
        self.reserve(version="2")

    def test_start_is_durable_and_does_not_wait_for_job(self):
        response = jobs.start(self.session, {}, 1, "model-a", StartModelPerfRequest(
            model_version="1", input_data='{"data":[{"INPUT":[1]}]}',
            dockerconfigjson='{"auths":{"registry":{"auth":"private"}}}',
        ))
        self.assertEqual(response.state, "creating")
        self.api.create.assert_not_called()
        self.assertNotIn("private", response.model_dump_json())
        row = self.session.get(ModelPerfJobEntity, response.id)
        self.assertTrue(row.prepared)
        self.assertIn("/perf-input/input.json", row.command)

    def test_prepare_failure_is_terminal_without_credential_disclosure(self):
        self.api.prepare.side_effect = RuntimeError("secret credentials")
        response = jobs.start(self.session, {}, 1, "model-a", StartModelPerfRequest(model_version="1"))
        self.assertEqual(response.state, "failed")
        self.assertNotIn("secret credentials", response.model_dump_json())

    def test_restart_recovers_creation_then_resumes_exact_job(self):
        run = self.reserve()
        job = fake_job(run, suspend=True)
        self.api.create.return_value = job
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "pending")
        self.api.resume.assert_not_called()
        self.api.read.return_value = job
        with Session(self.engine) as restarted:
            jobs.reconcile(restarted, run.id)
        self.api.resume.assert_called_once()
        self.assertEqual(self.api.create.call_count, 1)

    def test_ambiguous_creation_recovers_existing_suspended_job(self):
        run = self.reserve()
        self.api.create.side_effect = TimeoutError()
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "creating")
        self.assertIn("retrying", run.message)
        self.api.read.return_value = fake_job(run, suspend=True)
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "pending")
        self.assertEqual(self.api.create.call_count, 1)

    def test_stop_persists_logs_before_deletion_and_blocks_until_pods_gone(self):
        run = self.reserve()
        run.state = "running"
        self.session.add(run)
        self.session.commit()
        self.api.read.return_value = fake_job(run)
        self.api.pods.return_value = [fake_pod()]
        self.api.logs.return_value = "partial output"
        response = jobs.stop(self.session, {}, 1, "model-a", run.id)
        self.assertEqual(response.state, "stopping")
        self.api.delete_job.assert_not_called()
        with Session(self.engine) as reader:
            self.assertEqual(reader.get(ModelPerfJobEntity, run.id).output, "partial output")
        jobs.reconcile(self.session, run.id)
        self.api.delete_job.assert_called_once()
        with self.assertRaises(ConflictError):
            self.reserve()
        self.api.read.return_value = None
        self.api.pods.return_value = []
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "cancelled")
        newer = self.reserve()
        jobs.stop(self.session, {}, 1, "model-a", run.id)
        self.assertEqual(newer.state, "creating")

    def test_stop_during_creation_never_resumes_late_suspended_job(self):
        run = self.reserve()
        jobs.stop(self.session, {}, 1, "model-a", run.id)
        self.api.read.return_value = fake_job(run, suspend=True)
        jobs.reconcile(self.session, run.id)
        self.api.resume.assert_not_called()
        self.api.delete_job.assert_called_once()
        self.assertEqual(run.state, "stopping")

    def test_completed_run_keeps_results_before_cleanup_and_allows_rerun(self):
        run = self.reserve()
        run.state = "running"
        self.session.add(run)
        self.session.commit()
        self.api.read.return_value = fake_job(run, conditions=[NS(type="Complete", status="True")])
        self.api.pods.return_value = [fake_pod("Succeeded", 0)]
        self.api.logs.return_value = "throughput: 123"
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "succeeded")
        self.api.cleanup.assert_not_called()
        saved = self.session.exec(select(PerfAnalyzerRunEntity)).one()
        self.assertEqual(saved.output, "throughput: 123")
        jobs.reconcile(self.session, run.id)
        self.assertTrue(run.cleaned)
        self.reserve()

    def test_outage_does_not_release_slot(self):
        run = self.reserve()
        self.api.read.side_effect = ApiException(status=503)
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "creating")
        self.assertIn("503", run.message)
        with self.assertRaises(ConflictError):
            self.reserve()

    def test_missing_job_only_releases_after_orphaned_pods_are_gone(self):
        run = self.reserve()
        run.state = "running"
        self.session.add(run)
        self.session.commit()
        self.api.pods.return_value = [fake_pod()]
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "running")
        self.api.pods.return_value = []
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "failed")

    def test_interrupted_preparation_and_rejected_image_fail(self):
        run = self.reserve()
        run.prepared = False
        run.created_at = datetime.utcnow() - timedelta(minutes=2)
        self.session.add(run)
        self.session.commit()
        jobs.reconcile(self.session, run.id)
        self.assertEqual(run.state, "failed")
        new = self.reserve()
        self.api.create.side_effect = ApiException(status=422)
        jobs.reconcile(self.session, new.id)
        self.assertEqual(new.state, "failed")

    def test_instance_deletion_prevents_new_runs_and_retains_unfinished_tracking(self):
        run = self.reserve()
        with self.assertRaises(ConflictError):
            jobs.prepare_instance_deletion(self.session, 1)
        self.assertIsNotNone(self.session.get(ModelPerfJobEntity, run.id))
        with self.assertRaisesRegex(ConflictError, "deletion"):
            self.reserve(model="model-b")
        jobs.reconcile(self.session, run.id)
        jobs.reconcile(self.session, run.id)
        jobs.prepare_instance_deletion(self.session, 1)
        self.assertIsNone(self.session.get(ModelPerfJobEntity, run.id))

    def test_all_api_operations_enforce_instance_access(self):
        for operation in (
            lambda: jobs.start(self.session, {}, 1, "a", StartModelPerfRequest(model_version="1")),
            lambda: jobs.status(self.session, {}, 1, "a", "1"),
            lambda: jobs.get_run(self.session, {}, 1, "a", "id"),
            lambda: jobs.stop(self.session, {}, 1, "a", "id"),
        ):
            with patch.object(jobs, "get_instance_or_404", side_effect=ForbiddenError("denied")):
                with self.assertRaises(ForbiddenError):
                    operation()

    def test_http_contract_and_removed_global_api(self):
        app.dependency_overrides[get_claims] = lambda: {}
        app.dependency_overrides[get_session] = lambda: None
        run = make_run()
        with patch.object(jobs, "start", return_value=jobs.dto(run)):
            response = TestClient(app).post("/api/instances/1/models/model-a/perf/runs", json={"model_version": "1"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["id"], run.id)
        self.assertNotIn("/api/perf-analyzers", app.openapi()["paths"])


class PerfManifestTests(unittest.TestCase):
    def test_missing_namespace_has_no_pods_but_outages_are_not_treated_as_absence(self):
        api = Jobs.__new__(Jobs)
        api.core = MagicMock()
        api.core.list_namespaced_pod.side_effect = ApiException(status=404)
        self.assertEqual(api.pods(make_run()), [])
        api.core.list_namespaced_pod.side_effect = ApiException(status=503)
        with self.assertRaises(ApiException):
            api.pods(make_run())

    def test_legacy_retirement_waits_for_pods_and_never_deletes_namespace(self):
        api = Jobs.__new__(Jobs)
        api.apps, api.core = MagicMock(), MagicMock()
        deployment = NS(metadata=NS(uid="legacy"), spec=NS(template=NS(metadata=NS(labels={"app": "perf-analyzer"}))))
        api.apps.read_namespaced_deployment.return_value = deployment
        self.assertFalse(api.retire_legacy("shared", "legacy-perf"))
        body = api.apps.delete_namespaced_deployment.call_args.kwargs["body"]
        self.assertEqual(body.preconditions.uid, "legacy")
        api.apps.read_namespaced_deployment.side_effect = ApiException(status=404)
        api.core.list_namespaced_pod.return_value = NS(items=[fake_pod()])
        self.assertFalse(api.retire_legacy("shared", "legacy-perf"))
        api.core.delete_namespaced_secret.assert_not_called()
        api.core.list_namespaced_pod.return_value = NS(items=[])
        self.assertTrue(api.retire_legacy("shared", "legacy-perf"))
        api.core.delete_namespace.assert_not_called()
        api.core.delete_namespaced_pod.assert_not_called()

    def test_legacy_retirement_refuses_replaced_deployment(self):
        api = Jobs.__new__(Jobs)
        api.apps, api.core = MagicMock(), MagicMock()
        api.apps.read_namespaced_deployment.return_value = NS(
            spec=NS(template=NS(metadata=NS(labels={"app": "other"}))),
        )
        with self.assertRaises(ValueError):
            api.retire_legacy("shared", "legacy-perf")
        api.apps.delete_namespaced_deployment.assert_not_called()

    def test_security_and_input_isolation(self):
        a, b = make_run(), make_run(model="b")
        a.command += ["--input-data", "/perf-input/input.json"]
        a.pull_secret = True
        value = manifest(a)
        pod = value["spec"]["template"]["spec"]
        self.assertTrue(value["spec"]["suspend"])
        self.assertEqual(value["spec"]["backoffLimit"], 0)
        self.assertEqual(pod["securityContext"]["runAsUser"], 10001)
        self.assertEqual(pod["securityContext"]["runAsGroup"], 10001)
        self.assertEqual(pod["securityContext"]["fsGroup"], 10001)
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])
        self.assertFalse(pod["automountServiceAccountToken"])
        security = pod["containers"][0]["securityContext"]
        self.assertFalse(security["allowPrivilegeEscalation"])
        self.assertTrue(security["readOnlyRootFilesystem"])
        self.assertEqual(security["capabilities"]["drop"], ["ALL"])
        self.assertEqual(pod["volumes"][-1]["secret"]["secretName"], a.job_name + "-input")
        self.assertNotIn(a.id, str(manifest(b)))

    def test_deletion_uses_exact_uid_and_rejects_foreign_resources(self):
        api = Jobs.__new__(Jobs)
        api.batch, api.core = MagicMock(), MagicMock()
        run = make_run()
        api.delete_job(run, fake_job(run), [])
        body = api.batch.delete_namespaced_job.call_args.kwargs["body"]
        self.assertEqual(body.preconditions.uid, run.id)
        self.assertEqual(body.propagation_policy, "Foreground")
        foreign = fake_job(make_run())
        with self.assertRaises(ValueError):
            api.check_owner(foreign, run)


@unittest.skipUnless(os.getenv("PERF_TEST_DATABASE_URL"), "Dedicated PostgreSQL test database not configured")
class PostgresPerfConcurrencyTests(unittest.TestCase):
    def test_workers_share_one_model_slot(self):
        engine = create_engine(os.environ["PERF_TEST_DATABASE_URL"])
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            instance = TritonInstanceEntity(name="perf-" + uuid4().hex, url="http://test")
            session.add(instance)
            session.commit()
            instance_id = instance.id
        barrier = Barrier(2)

        def attempt(version):
            with Session(engine) as session:
                barrier.wait(timeout=10)
                try:
                    repo.reserve(session, make_run(version=version, instance_id=instance_id))
                    return "accepted"
                except ConflictError:
                    return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ["1", "2"]))
        self.assertCountEqual(outcomes, ["accepted", "conflict"])
        engine.dispose()
