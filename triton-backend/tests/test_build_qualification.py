"""Arrange–Act–Assert tests for fail-closed qualification and immutable policy binding."""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from pydantic import ValidationError
from sqlmodel import Session, create_engine

from app.db.schema import create_tables
from app.exceptions import ForbiddenError
from app.repositories import build_qualifications
from app.schemas.build_policy import BuildPolicy
from app.services.builds.manifests import job
from app.services.builds.qualification import REQUIRED_CHECKS, all_checks_passed, require_qualified


def policy(**changes):
    return BuildPolicy.model_validate(
        {
            "executor_image": "registry.example/kaniko@sha256:" + "a" * 64,
            "release": "v1.29.1",
            "namespace": "build-test",
            "s3_profile_name": "staging",
            "staging_prefix": "builds/",
            "allowed_repositories": ["registry.example/allowed"],
            "base_pull_secret": "base",
            "push_secret": "push",
            "runtime_pull_secret": "runtime",
            "provisioning_revision": "revision-1",
            "egress_cidrs": ["192.0.2.1/32"],
            **changes,
        }
    )


class BuildPolicyTests(unittest.TestCase):
    def test_invalid_policies_fail_closed(self):
        # Arrange
        invalid = [
            {"executor_image": "registry.example/kaniko:latest"},
            {"push_secret": "base"},
            {"egress_cidrs": ["0.0.0.0/0"]},
            {"staging_prefix": "../outside/"},
            {"allowed_repositories": ["registry.example/*"]},
            {"egress_ports": []},
        ]
        # Act / Assert
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                policy(**change)

    def test_exact_repository_scope_rejects_prefix_and_tag_bypasses(self):
        # Arrange
        configured = policy()
        # Act
        allowed = configured.permits_destination("registry.example/allowed:v1")
        denied = [
            configured.permits_destination(value)
            for value in [
                "registry.example/allowed-other:v1",
                "registry.example/allowed/child:v1",
                "registry.example/allowed@sha256:" + "a" * 64,
                "registry.example/allowed:",
            ]
        ]
        # Assert
        self.assertTrue(allowed)
        self.assertFalse(any(denied))

    def test_resource_image_and_credential_changes_invalidate_binding(self):
        # Arrange
        configured = policy()
        # Act
        changed = [
            policy(cpu_millis=2000),
            policy(provisioning_revision="rotated"),
            policy(executor_image="registry.example/kaniko@sha256:" + "b" * 64),
        ]
        # Assert
        self.assertTrue(all(item.fingerprint() != configured.fingerprint() for item in changed))
        self.assertEqual(policy(enabled=True).fingerprint(), configured.fingerprint())

    def test_job_has_bounded_resources_and_no_host_or_identity_mounts(self):
        # Arrange
        configured = policy()
        # Act
        rendered = job(
            configured, "build", "s3://bucket/builds/context.tar.gz", "registry.example/allowed:v1", "scoped"
        )
        pod = rendered["spec"]["template"]["spec"]
        # Assert
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertFalse(any("hostPath" in volume for volume in pod["volumes"]))
        self.assertEqual(rendered["spec"]["activeDeadlineSeconds"], configured.timeout_seconds)
        self.assertEqual(pod["containers"][0]["image"], configured.executor_image)
        self.assertFalse(pod["containers"][0]["securityContext"]["allowPrivilegeEscalation"])
        with self.assertRaises(ValueError):
            job(configured, "bad", "s3://bucket/builds/context.tar.gz", "registry.example/outside:v1", "scoped")


class QualificationGateTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        create_tables(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

    def record(self, configured, checks):
        return build_qualifications.append(
            self.session,
            policy_hash=configured.fingerprint(),
            fixture_revision="fixture-1",
            executor_image=configured.executor_image,
            platform=configured.platform,
            passed=all_checks_passed(checks),
            checks=checks,
            evidence_path="/evidence/report.json",
            evidence_sha256=uuid4().hex,
        )

    def test_missing_partial_and_failed_evidence_never_enable_execution(self):
        # Arrange
        configured = policy(enabled=True)
        # Act / Assert
        for checks in [None, {"provenance": True}, dict.fromkeys(REQUIRED_CHECKS, False)]:
            if checks is not None:
                self.record(configured, checks)
            with self.assertRaises(ForbiddenError):
                require_qualified(self.session, configured, "fixture-1", mode="development")

    def test_success_is_bound_to_mode_enabled_flag_policy_and_fixture(self):
        # Arrange
        configured = policy(enabled=True)
        self.record(configured, dict.fromkeys(REQUIRED_CHECKS, True))
        # Act
        require_qualified(self.session, configured, "fixture-1", mode="development")
        # Assert
        for candidate, revision, mode in [
            (policy(), "fixture-1", "development"),
            (configured, "fixture-1", "production"),
            (policy(enabled=True, cpu_millis=2000), "fixture-1", "development"),
            (configured, "fixture-2", "development"),
        ]:
            with self.assertRaises(ForbiddenError):
                require_qualified(self.session, candidate, revision, mode=mode)

    def test_later_failed_attempt_overrides_earlier_success(self):
        # Arrange
        configured = policy(enabled=True)
        self.record(configured, dict.fromkeys(REQUIRED_CHECKS, True))
        # Act
        self.record(configured, dict.fromkeys(REQUIRED_CHECKS, False))
        # Assert
        with self.assertRaises(ForbiddenError):
            require_qualified(self.session, configured, "fixture-1", mode="development")


@unittest.skipUnless(os.getenv("BUILD_QUALIFICATION_TEST_DATABASE_URL"), "Set isolated PostgreSQL test URL")
class PostgresQualificationTests(unittest.TestCase):
    def test_concurrent_startup_and_append_keep_independent_reports(self):
        # Arrange: the supplied database must be a disposable test database.
        engine = create_engine(os.environ["BUILD_QUALIFICATION_TEST_DATABASE_URL"])
        self.addCleanup(engine.dispose)
        configured = policy()
        revision = "test-" + uuid4().hex

        def attempt(index):
            create_tables(engine)
            with Session(engine) as session:
                return build_qualifications.append(
                    session,
                    policy_hash=configured.fingerprint(),
                    fixture_revision=revision,
                    executor_image=configured.executor_image,
                    platform=configured.platform,
                    passed=False,
                    checks={"provenance": bool(index)},
                    evidence_path="/evidence/report.json",
                    evidence_sha256=uuid4().hex,
                ).id

        # Act
        with ThreadPoolExecutor(max_workers=3) as pool:
            ids = list(pool.map(attempt, range(3)))
        create_tables(engine)

        # Assert
        self.assertEqual(len(set(ids)), 3)
        with Session(engine) as session:
            latest = build_qualifications.latest(session, configured.fingerprint(), revision)
            self.assertIn(latest.id, ids)
            self.assertFalse(latest.passed)
