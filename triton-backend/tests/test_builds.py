"""Arrange–Act–Assert regression tests for owned durable build execution."""

import base64
import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
from uuid import uuid4

from sqlmodel import Session

from app.db.entities import BuildAttemptEntity, PreparationTaskEntity
from app.exceptions import ConflictError, ForbiddenError
from app.repositories import build_qualifications, builds, operations, teams
from app.schemas.build_policy import BuildPolicy
from app.schemas.builds import BuildRequest
from app.services.builds import contexts_cleanup, execution, jobs_kubernetes
from app.services.builds.qualification import REQUIRED_CHECKS
from app.services.operations import OperationWorker
from tests import test_build_contexts as context_fixtures

DIGEST = "sha256:" + "d" * 64


class FakeJobs:
    def __init__(self):
        self.resource = None
        self.creates = 0
        self.deletes = 0
        self.observation = jobs_kubernetes.Observation()
        self.lose_ack = False
        self.cleanup_error = False

    def close(self):
        pass

    def read(self, row):
        if self.resource and row.job_uid and self.resource.metadata.uid != row.job_uid:
            raise ConflictError("UID changed")
        return self.resource

    def provision(self, row, url, ca_certificate=""):
        pass

    def create(self, row):
        self.creates += 1
        self.resource = NS(metadata=NS(uid="uid-" + row.job_name), spec=NS(suspend=True))
        if self.lose_ack:
            self.lose_ack = False
            raise OSError("lost response")
        return self.resource

    def resume(self, row, resource):
        resource.spec.suspend = False

    def observe(self, row, resource):
        return self.observation

    def cleanup(self, row):
        if self.cleanup_error:
            raise OSError("lost cleanup response")
        self.deletes += 1
        self.resource = None
        return True


class BuildTests(unittest.TestCase):
    policy: BuildPolicy

    def seed(self):
        context_fixtures.ContextTests.seed(self)

    def storage(self, session, expected):
        return context_fixtures.ContextTests.storage(self, session, expected)

    def upload(self, context_id):
        return context_fixtures.ContextTests.upload(self, context_id)

    def submit_context(self):
        return context_fixtures.ContextTests.submit(self)

    def run_context(self, context_id):
        return context_fixtures.ContextTests.run_worker(self, context_id)

    def setUp(self):
        context_fixtures.ContextTests.setUp(self)
        self.seed_builds()

    def seed_builds(self):
        self.policy = self.policy.model_copy(update={"enabled": True})
        self.task_id = uuid4()
        self.provider = FakeJobs()
        self.store.ca_certificate = ""
        self.store.download_url = lambda *args: "https://storage.test/context?versionId=one&signature=secret"
        self.service.register(execution.KIND, execution.adapter)
        for p in (patch.object(execution, "service", self.service),
                  patch.object(jobs_kubernetes, "Jobs", return_value=self.provider),
                  patch.dict(os.environ, {"TRITON_BUILD_FIXTURE_REVISION": "fixture",
                                          "TRITON_CONTROL_MODE": "development"})):
            p.start()
            self.addCleanup(p.stop)
        with Session(self.engine) as session:
            session.add(PreparationTaskEntity(id=self.task_id, title="Runtime",
                                               assigned_team_id=teams.INSTALLATION_TEAM_ID,
                                               submitter_user_id=1))
            session.commit()
            build_qualifications.append(session, policy_hash=self.policy.fingerprint(), fixture_revision="fixture",
                executor_image=self.policy.executor_image, platform=self.policy.platform, passed=True,
                checks=dict.fromkeys(REQUIRED_CHECKS, True), evidence_path="fixture", evidence_sha256="a" * 64)
        context_id = self.submit_context().id
        self.upload(context_id)
        self.run_context(context_id)
        self.request = BuildRequest(task_id=self.task_id, context_id=context_id,
                                    repository=self.policy.allowed_repositories[0])

    def submit_build(self, key="build-1", claims=None):
        with Session(self.engine) as session:
            return execution.submit(session, claims or self.claims, self.request, key)

    def run_build(self, build_id):
        with Session(self.engine) as session:
            op = operations.get(session, build_id)
            op.available_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(op)
            session.commit()
        OperationWorker(self.service, lambda: Session(self.engine)).run_once()
        with Session(self.engine) as session:
            return execution.read(session, self.claims, build_id)

    def finish(self, build_id, success=True):
        self.provider.observation = jobs_kubernetes.Observation(True, success, DIGEST if success else None, "build log")
        self.run_build(build_id)
        return self.run_build(build_id)

    def test_success_retains_reported_digest_and_logs_before_cleanup(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.run_build(build_id)
        self.provider.observation = jobs_kubernetes.Observation(True, True, DIGEST, "build log")
        # Act
        retained = self.run_build(build_id)
        # Assert
        self.assertEqual(self.provider.deletes, 0)
        self.assertEqual(retained.attempts[0].final_digest, DIGEST)
        with Session(self.engine) as session:
            self.assertEqual(execution.logs(session, self.claims, build_id, 1).output, "build log")
        completed = self.run_build(build_id)
        self.assertEqual(completed.operation.state, "succeeded")
        self.assertTrue(completed.attempts[0].cleaned)
        self.assertEqual(self.provider.creates, 1)

    def test_lost_create_response_recovers_same_job_after_restart(self):
        # Arrange
        build_id = self.submit_build().id
        self.provider.lose_ack = True
        # Act
        self.run_build(build_id)
        recovered = self.run_build(build_id)
        self.run_build(build_id)
        # Assert
        self.assertEqual(self.provider.creates, 1)
        self.assertEqual(recovered.attempts[0].job_uid, self.provider.resource.metadata.uid)
        self.assertFalse(self.provider.resource.spec.suspend)

    def test_restart_after_result_commit_retries_cleanup_without_building(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.provider.observation = jobs_kubernetes.Observation(True, True, DIGEST, "retained")
        self.run_build(build_id)
        self.provider.cleanup_error = True
        # Act
        self.run_build(build_id)
        self.provider.cleanup_error = False
        recovered = self.run_build(build_id)
        # Assert
        self.assertEqual(recovered.operation.state, "succeeded")
        self.assertEqual(self.provider.creates, 1)
        with Session(self.engine) as session:
            self.assertEqual(builds.latest(session, build_id).logs, "retained")

    def test_cancel_before_admission_does_not_create_job(self):
        # Arrange
        build_id = self.submit_build().id
        with Session(self.engine) as session:
            execution.cancel(session, self.claims, build_id)
        # Act
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "cancelled")
        self.assertEqual(self.provider.creates, 0)

    def test_cancel_running_build_retains_logs_and_omits_digest(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.provider.observation = jobs_kubernetes.Observation(False, False, None, "partial")
        with Session(self.engine) as session:
            execution.cancel(session, self.claims, build_id)
        # Act
        self.run_build(build_id)
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "cancelled")
        self.assertIsNone(result.attempts[0].final_digest)
        with Session(self.engine) as session:
            self.assertEqual(builds.latest(session, build_id).logs, "partial")

    def test_timeout_retains_evidence_and_cleans_up(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        with Session(self.engine) as session:
            row = builds.latest(session, build_id)
            row.deadline_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        # Act
        self.run_build(build_id)
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "failed")
        self.assertTrue(result.attempts[0].cleaned)

    def test_explicit_retry_gets_unique_tag_and_preserves_old_attempt(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.finish(build_id, success=False)
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, build_id)
        self.provider.observation = jobs_kubernetes.Observation()
        # Act
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(len(result.attempts), 2)
        self.assertNotEqual(result.attempts[0].destination, result.attempts[1].destination)
        self.assertEqual(result.attempts[0].state, "failed")
        self.assertEqual(self.provider.creates, 2)

    def test_uid_substitution_is_never_deleted_or_adopted(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.provider.resource.metadata.uid = "foreign"
        with Session(self.engine) as session:
            execution.cancel(session, self.claims, build_id)
        # Act
        result = self.run_build(build_id)
        # Assert
        self.assertNotEqual(result.operation.state, "cancelled")
        self.assertEqual(self.provider.deletes, 0)

    def test_idempotency_and_owner_authorization(self):
        # Arrange
        first = self.submit_build()
        # Act
        replay = self.submit_build()
        # Assert
        self.assertEqual(first.id, replay.id)
        with Session(self.engine) as session:
            for fn in (execution.read, execution.cancel):
                with self.assertRaises(ForbiddenError):
                    fn(session, {"email": "other@test"}, first.id)
            with self.assertRaises(ForbiddenError):
                execution.logs(session, {"email": "other@test"}, first.id, 1)
        with self.assertRaises(ForbiddenError):
            self.submit_build(claims={**self.claims, "token_use": "workspace_deployment"})

    def test_production_and_unqualified_policy_deny_creation(self):
        # Arrange / Act / Assert
        with patch.dict(os.environ, {"TRITON_CONTROL_MODE": "production"}), self.assertRaises(ForbiddenError):
            self.submit_build()
        self.policy = self.policy.model_copy(update={"enabled": False})
        with self.assertRaises(ForbiddenError):
            self.submit_build()
        self.policy = self.policy.model_copy(update={"enabled": True, "provisioning_revision": "changed"})
        with self.assertRaises(ForbiddenError):
            self.submit_build()

    def test_active_build_pins_context_and_release_reference_pins_logs(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        from app.repositories import build_contexts
        with Session(self.engine) as session:
            row = build_contexts.get(session, self.request.context_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            row.upload_until = None
            session.add(row)
            session.commit()
        # Act
        swept = contexts_cleanup.cleanup(lambda: Session(self.engine))
        # Assert
        self.assertEqual(swept["removed"], 0)
        self.finish(build_id)
        with Session(self.engine) as session:
            builds.retain_for_release(session, build_id, 1, "sha256:" + "e" * 64)
            session.commit()
            self.assertEqual(builds.expire_logs(session, datetime.utcnow() + timedelta(days=31)), 0)
            self.assertEqual(builds.latest(session, build_id).logs, "build log")

    def test_unreferenced_logs_expire_but_digest_survives(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.finish(build_id)
        # Act
        with Session(self.engine) as session:
            count = builds.expire_logs(session, datetime.utcnow() + timedelta(days=31))
            row = builds.latest(session, build_id)
            # Assert
            self.assertEqual(count, 1)
            self.assertTrue(row.logs_expired)
            self.assertEqual(row.logs, "")
            self.assertEqual(row.final_digest, DIGEST)

    def test_cancel_queued_retry_never_starts_another_attempt(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.finish(build_id, success=False)
        with Session(self.engine) as session:
            self.service.retry(session, self.claims, build_id)
            execution.cancel(session, self.claims, build_id)
        # Act
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "cancelled")
        self.assertEqual(self.provider.creates, 1)

    def test_policy_change_prevents_activation_but_allows_cancellation(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.policy = self.policy.model_copy(update={"enabled": False})
        # Act
        self.run_build(build_id)
        # Assert
        self.assertTrue(self.provider.resource.spec.suspend)
        with Session(self.engine) as session:
            execution.cancel(session, self.claims, build_id)
        self.run_build(build_id)
        self.assertEqual(self.run_build(build_id).operation.state, "cancelled")

    def test_missing_job_cannot_be_rebuilt_under_same_attempt_tag(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        self.provider.resource = None
        # Act
        self.run_build(build_id)
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "failed")
        self.assertEqual(self.provider.creates, 1)

    def test_context_expiring_in_queue_fails_without_launching(self):
        # Arrange
        from app.repositories import build_contexts

        build_id = self.submit_build().id
        with Session(self.engine) as session:
            row = build_contexts.get(session, self.request.context_id)
            row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
        # Act
        result = self.run_build(build_id)
        # Assert
        self.assertEqual(result.operation.state, "failed")
        self.assertEqual(self.provider.creates, 0)

    def test_cancellation_between_checkpoint_and_write_is_fenced(self):
        # Arrange
        build_id = self.submit_build().id
        self.run_build(build_id)
        with Session(self.engine) as session:
            row = operations.get(session, build_id)
            row.available_at = datetime.utcnow() - timedelta(seconds=1)
            session.add(row)
            session.commit()
            claimed = operations.claim(session, "race-worker", [execution.KIND], 30)
        context = MagicMock(sessions=lambda: Session(self.engine))

        def cancel_at_checkpoint():
            with Session(self.engine) as session:
                operations.cancel(session, build_id)

        context.checkpoint.side_effect = cancel_at_checkpoint
        # Act / Assert
        with self.assertRaises(ConflictError):
            execution.adapter.reconcile(claimed, context)
        self.assertTrue(self.provider.resource.spec.suspend)

    def test_old_qualification_without_versioned_transport_cannot_enable_builds(self):
        # Arrange
        checks = dict.fromkeys(REQUIRED_CHECKS, True)
        checks.pop("versioned_https_context")
        with Session(self.engine) as session:
            build_qualifications.append(session, policy_hash=self.policy.fingerprint(), fixture_revision="fixture",
                executor_image=self.policy.executor_image, platform=self.policy.platform, passed=True,
                checks=checks, evidence_path="old", evidence_sha256="b" * 64)
        # Act / Assert
        with self.assertRaises(ForbiddenError):
            self.submit_build()

    def test_api_create_status_logs_cancel_and_ownership(self):
        # Arrange
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.build_api import router
        from app.core.security import get_claims
        from app.db.database import get_session

        app = FastAPI()
        app.include_router(router)

        def session_dependency():
            with Session(self.engine) as session:
                yield session

        app.dependency_overrides[get_session] = session_dependency
        app.dependency_overrides[get_claims] = lambda: self.claims
        with TestClient(app) as client:
            # Act
            created = client.post("/api/builds", json=self.request.model_dump(mode="json"),
                                  headers={"Idempotency-Key": "http-build"})
            # Assert
            self.assertEqual(created.status_code, 202)
            build_id = created.json()["id"]
            self.assertEqual(created.headers["Location"], f"/api/builds/{build_id}")
            self.run_build(build_id)
            self.assertEqual(client.get(f"/api/builds/{build_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/builds/{build_id}/logs").status_code, 200)
            app.dependency_overrides[get_claims] = lambda: {"email": "other@test"}
            self.assertEqual(client.get(f"/api/builds/{build_id}/logs").status_code, 403)
            self.assertEqual(client.post(f"/api/builds/{build_id}/cancel").status_code, 403)
            app.dependency_overrides[get_claims] = lambda: self.claims
            self.assertEqual(client.post(f"/api/builds/{build_id}/cancel").status_code, 202)


class ProviderTests(unittest.TestCase):
    def test_provision_uses_only_base_and_push_credentials_and_recovers_existing_secret(self):
        # Arrange
        from tests.test_build_qualification import policy

        jobs = object.__new__(jobs_kubernetes.Jobs)
        jobs.core = MagicMock()
        row = BuildAttemptEntity(operation_id="a" * 32, number=1, namespace="build-test", job_name="build",
                                 policy=policy().model_dump())

        def secret(host, credential):
            config = json.dumps({"auths": {host: {"auth": credential}}}).encode()
            return NS(data={".dockerconfigjson": base64.b64encode(config).decode()})

        jobs.core.read_namespaced_secret.side_effect = [secret("base.example", "base-auth"),
                                                        secret("push.example", "push-auth")]
        with patch.object(jobs, "secret", return_value=None):
            # Act
            jobs.provision(row, "https://storage.test/version", "fixture-ca")
        # Assert
        self.assertEqual([c.args[0] for c in jobs.core.read_namespaced_secret.call_args_list], ["base", "push"])
        body = jobs.core.create_namespaced_secret.call_args.args[1]
        self.assertTrue(body["immutable"])
        self.assertEqual(body["stringData"]["ca.pem"], "fixture-ca")
        self.assertEqual(set(json.loads(body["stringData"]["config.json"])["auths"]),
                         {"base.example", "push.example"})
        with patch.object(jobs, "secret", return_value=NS()):
            jobs.provision(row, "https://storage.test/new-signature")
        self.assertEqual(jobs.core.create_namespaced_secret.call_count, 1)

    def test_conflicting_credential_scopes_are_rejected_before_secret_write(self):
        # Arrange
        from tests.test_build_qualification import policy

        jobs = object.__new__(jobs_kubernetes.Jobs)
        jobs.core = MagicMock()
        row = BuildAttemptEntity(policy=policy().model_dump(), namespace="build-test")
        jobs.core.read_namespaced_secret.side_effect = [NS(data={"config.json": base64.b64encode(
            json.dumps({"auths": {"registry.example": {"auth": auth}}}).encode()).decode(),
        }) for auth in ("base-auth", "push-auth")]
        # Act / Assert
        with patch.object(jobs, "secret", return_value=None), self.assertRaises(ConflictError):
            jobs.provision(row, "https://storage.test/version")
        jobs.core.create_namespaced_secret.assert_not_called()

    def test_controller_version_race_requires_reobservation(self):
        # Arrange
        from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

        jobs = object.__new__(jobs_kubernetes.Jobs)
        jobs.batch = MagicMock()
        jobs.batch.patch_namespaced_job.side_effect = ApiException(status=409)
        row = BuildAttemptEntity(namespace="build-test", job_name="build")
        # Act
        resumed = jobs.resume(row, NS(metadata=NS(resource_version="old")))
        # Assert
        self.assertFalse(resumed)
        self.assertEqual(jobs.batch.patch_namespaced_job.call_args.args[2]["metadata"], {"resourceVersion": "old"})

    def test_only_successful_owned_executor_can_report_final_digest(self):
        # Arrange
        jobs = object.__new__(jobs_kubernetes.Jobs)
        jobs.core = MagicMock()
        row = BuildAttemptEntity(operation_id="a" * 32, number=1, namespace="build-test", job_name="build")
        resource = NS(metadata=NS(uid="job-uid"), status=NS(conditions=[NS(type="Complete", status="True")]))
        terminated = NS(exit_code=0, message=DIGEST)
        pod = NS(metadata=NS(name="pod", owner_references=[NS(uid="job-uid", kind="Job", controller=True)]),
                 status=NS(container_statuses=[NS(name="executor", state=NS(running=None, terminated=terminated))]))
        jobs.core.list_namespaced_pod.return_value.items = [pod]
        jobs.core.read_namespaced_pod_log.return_value = "log"
        with patch.object(jobs, "sensitive_values", return_value=[]):
            # Act / Assert
            self.assertEqual(jobs.observe(row, resource).digest, DIGEST)
            for message in ("", "sha256:bad", DIGEST + "\n" + DIGEST, "registry/image@" + DIGEST):
                terminated.message = message
                self.assertFalse(jobs.observe(row, resource).succeeded)
                self.assertIsNone(jobs.observe(row, resource).digest)
            terminated.message, terminated.exit_code = DIGEST, 1
            self.assertFalse(jobs.observe(row, resource).succeeded)
            pod.metadata.owner_references[0].uid = "foreign"
            jobs.core.read_namespaced_pod_log.reset_mock()
            self.assertIsNone(jobs.observe(row, resource).digest)
            jobs.core.read_namespaced_pod_log.assert_not_called()

    def test_signed_url_is_version_pinned_and_http_is_denied(self):
        # Arrange
        from app.services.builds.contexts_storage import ContextStore
        client = MagicMock()
        client.generate_presigned_url.return_value = "https://storage.test/key?versionId=immutable"
        store = ContextStore(client, "bucket")
        # Act
        store.download_url("key", "immutable", 300)
        # Assert
        self.assertEqual(client.generate_presigned_url.call_args.kwargs["Params"]["VersionId"], "immutable")
        client.generate_presigned_url.return_value = "http://storage.test/key"
        with self.assertRaises(ForbiddenError):
            store.download_url("key", "immutable", 300)

    def test_manifest_is_suspended_bounded_and_has_no_autonomous_cleanup(self):
        # Arrange
        from tests.test_build_qualification import policy
        row = BuildAttemptEntity(operation_id="a" * 32, number=1, policy=policy().model_dump(), job_name="build-test",
            context_receipt={"storage_binding": {"bucket": "contexts"}, "key": "builds/one/context.tar.gz",
                             "dockerfile": "nested/Dockerfile"}, destination="registry.example/allowed:attempt")
        # Act
        body = jobs_kubernetes.manifest(row)
        pod = body["spec"]["template"]["spec"]
        # Assert
        self.assertTrue(body["spec"]["suspend"])
        self.assertNotIn("ttlSecondsAfterFinished", body["spec"])
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertNotIn("envFrom", pod["containers"][0])
        self.assertIn("--dockerfile=nested/Dockerfile", pod["containers"][0]["args"])
        self.assertNotIn("runtime", str(body))

    def test_redaction_removes_plain_encoded_credentials_and_urls(self):
        # Arrange
        output = "password secret auth c2VjcmV0 https://storage/key?X-Amz-Signature=abc\nordinary log"
        # Act
        safe = jobs_kubernetes.redact(output, ["secret", "c2VjcmV0"])
        # Assert
        self.assertNotIn("secret", safe)
        self.assertNotIn("c2VjcmV0", safe)
        self.assertNotIn("Signature", safe)
        self.assertIn("ordinary log", safe)
