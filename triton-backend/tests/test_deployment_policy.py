"""Deployment HTTP policy precedence, authorization and enforcement."""

import os
import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError, ForbiddenError
from app.schemas.deployment_access import DeploymentAccessRequest
from app.schemas.deployment_policy import DeploymentPolicyUpdate
from app.services.deployment.policy import (
    get_policy,
    resolve_hostname,
    short_hostname,
    update_policy,
    validate_endpoint_policy,
)


class DeploymentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        env = patch.dict(os.environ, {
            "TRITON_DEPLOY_ALLOW_HTTP": "false", "TRITON_DEPLOY_INGRESS_ENABLED": "true",
            "TRITON_DEPLOY_BASE_DOMAIN": "",
        })
        env.start()
        self.addCleanup(env.stop)

    def http(self, host="model.example.com"):
        return DeploymentAccessRequest(ingress_host=host, ingress_scheme="http")

    def test_default_is_https_only(self):
        # Arrange
        session = self.session
        # Act
        result = get_policy(session)
        # Assert
        self.assertFalse(result.allow_http)
        self.assertEqual(result.source, "environment")

    def test_default_rejects_new_http_endpoint(self):
        # Arrange
        request = self.http()
        # Act
        with self.assertRaises(BadRequestError) as raised:
            validate_endpoint_policy(request, self.session)
        # Assert
        self.assertIn("HTTP endpoints are disabled", str(raised.exception))

    def test_admin_override_takes_precedence_over_helm(self):
        # Arrange
        request = DeploymentPolicyUpdate(allow_http=False)
        with patch.dict(os.environ, {"TRITON_DEPLOY_ALLOW_HTTP": "true"}):
            # Act
            result = update_policy(request, self.session, {"role": "admin"})
        # Assert
        self.assertFalse(result.allow_http)
        self.assertTrue(result.default_allow_http)
        self.assertEqual(result.source, "admin")

    def test_reset_restores_helm_default(self):
        # Arrange
        update_policy(DeploymentPolicyUpdate(allow_http=False), self.session, {"role": "admin"})
        with patch.dict(os.environ, {"TRITON_DEPLOY_ALLOW_HTTP": "true"}):
            # Act
            result = update_policy(DeploymentPolicyUpdate(allow_http=None), self.session, {"role": "admin"})
        # Assert
        self.assertTrue(result.allow_http)
        self.assertEqual(result.source, "environment")

    def test_admin_override_survives_new_session(self):
        # Arrange
        update_policy(DeploymentPolicyUpdate(allow_http=True), self.session, {"role": "admin"})
        with Session(self.engine) as other:
            # Act
            result = get_policy(other)
        # Assert
        self.assertTrue(result.allow_http)

    def test_non_admin_cannot_update_policy(self):
        for role in ("member", "viewer"):
            with self.subTest(role=role):
                # Arrange
                request = DeploymentPolicyUpdate(allow_http=True)
                # Act
                with self.assertRaises(ForbiddenError):
                    update_policy(request, self.session, {"role": role})
                # Assert
                self.assertFalse(get_policy(self.session).allow_http)

    def test_existing_http_is_preserved(self):
        # Arrange
        request = self.http()
        # Act
        validate_endpoint_policy(
            request, self.session, existing_host=request.ingress_host, existing_scheme="http",
        )
        # Assert: the existing endpoint is accepted even though new HTTP is denied.
        self.assertFalse(get_policy(self.session).allow_http)

    def test_http_hostname_changes_and_https_downgrades_are_blocked(self):
        for host, scheme in (("new.example.com", "http"), ("model.example.com", "https")):
            with self.subTest(host=host, scheme=scheme):
                # Arrange
                request = self.http(host)
                # Act
                with self.assertRaises(BadRequestError) as raised:
                    validate_endpoint_policy(
                        request, self.session, existing_host="model.example.com", existing_scheme=scheme,
                    )
                # Assert
                self.assertIn("HTTP endpoints are disabled", str(raised.exception))

    def test_internal_and_https_remain_allowed(self):
        for host in (None, "model.example.com"):
            with self.subTest(host=host):
                # Arrange
                request = DeploymentAccessRequest(ingress_host=host)
                # Act
                validate_endpoint_policy(request, self.session)
                # Assert: these modes remain available under the HTTPS-only policy.
                self.assertFalse(get_policy(self.session).allow_http)

    def test_ingress_switch_cannot_be_overridden_in_ui(self):
        # Arrange
        update_policy(DeploymentPolicyUpdate(allow_http=True), self.session, {"role": "admin"})
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}):
            # Act
            result = get_policy(self.session)
        # Assert
        self.assertFalse(result.ingress_enabled)

    def test_ingress_switch_blocks_http_and_https(self):
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                # Arrange
                request = DeploymentAccessRequest(ingress_host="model.example.com", ingress_scheme=scheme)
                with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}):
                    # Act
                    with self.assertRaises(BadRequestError) as raised:
                        validate_endpoint_policy(request, self.session)
                # Assert
                self.assertIn("Model Ingress is disabled", str(raised.exception))

    def test_disabled_ingress_allows_internal_access(self):
        # Arrange
        request = DeploymentAccessRequest(access_mode="internal")
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}):
            # Act
            validate_endpoint_policy(request, self.session)
            # Assert: internal access remains available while Ingress is disabled.
            self.assertFalse(get_policy(self.session).ingress_enabled)

    def test_base_domain_resolves_short_hostname_without_mutating_input(self):
        # Arrange
        request = DeploymentAccessRequest(ingress_host="smollm", ingress_scheme="https")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            # Act
            resolved = resolve_hostname(request)
        # Assert
        self.assertEqual(resolved.ingress_host, "smollm.models.example.com")
        self.assertEqual(request.ingress_host, "smollm")

    def test_policy_reports_base_domain(self):
        # Arrange
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            # Act
            result = get_policy(self.session)
        # Assert
        self.assertEqual(result.base_domain, "models.example.com")

    def test_new_dotted_host_rejected_with_base_domain(self):
        # Arrange
        request = DeploymentAccessRequest(ingress_host="smollm.other.example")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            # Act
            with self.assertRaises(BadRequestError) as raised:
                resolve_hostname(request)
        # Assert
        self.assertIn("short hostname", str(raised.exception))

    def test_existing_full_host_is_not_appended_twice(self):
        # Arrange
        request = DeploymentAccessRequest(ingress_host="smollm.models.example.com")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            # Act
            result = resolve_hostname(request, existing_host=request.ingress_host)
        # Assert
        self.assertIs(result, request)

    def test_editable_hostname_preserves_legacy_domains(self):
        cases = (("smollm.models.example.com", "smollm"), ("legacy.other.example", "legacy.other.example"))
        for host, expected in cases:
            with self.subTest(host=host):
                # Arrange
                with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
                    # Act
                    result = short_hostname(host)
                # Assert
                self.assertEqual(result, expected)

    def test_empty_base_domain_preserves_standalone_host(self):
        # Arrange
        request = self.http("smollm")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": ""}):
            # Act
            result = resolve_hostname(request)
        # Assert
        self.assertIs(result, request)

    def test_invalid_domain_rejects_new_ingress(self):
        domains = ("*.models.example.com", "https://models.example.com", "models.example.com:443", "bad..domain")
        for domain in domains:
            with self.subTest(domain=domain):
                # Arrange
                request = self.http("smollm")
                with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": domain}):
                    # Act
                    with self.assertRaises(BadRequestError) as raised:
                        resolve_hostname(request)
                # Assert
                self.assertIn("base domain is invalid", str(raised.exception))

    def test_invalid_domain_does_not_block_internal_access(self):
        # Arrange
        request = DeploymentAccessRequest(access_mode="internal")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "*.models.example.com"}):
            # Act
            result = resolve_hostname(request)
        # Assert
        self.assertIs(result, request)

    def test_invalid_domain_preserves_existing_host(self):
        # Arrange
        request = DeploymentAccessRequest(ingress_host="legacy.example.com")
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "*.models.example.com"}):
            # Act
            result = resolve_hostname(request, existing_host=request.ingress_host)
        # Assert
        self.assertIs(result, request)

    def test_creation_schedules_resolved_hostname_once_and_returns_full_url(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.schemas import CreateDeploymentRequest
        from app.services.deployment import deployment
        # Arrange
        request = CreateDeploymentRequest(
            deployment_name="smollm", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", ingress_host="smollm",
        )
        schedule = Mock()
        with (
            patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}),
            patch.object(deployment, "resolve_profile_request", side_effect=lambda req, *args: req),
            patch.object(deployment, "upsert_deployed_instance", return_value=SimpleNamespace(id=1)),
            patch.object(deployment, "validate_new_hostname") as validate_host,
        ):
            # Act
            result = deployment.create_deployment(request, self.session, {"role": "member"}, schedule)
        # Assert
        self.assertEqual(result.external_url, "https://smollm.models.example.com")
        self.assertEqual(validate_host.call_args.args[0].ingress_host, "smollm.models.example.com")
        self.assertEqual(schedule.call_args.args[2].ingress_host, "smollm.models.example.com")
        self.assertEqual(request.ingress_host, "smollm")

    def test_invalid_domain_does_not_block_internal_deployment_creation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.schemas import CreateDeploymentRequest
        from app.services.deployment import deployment

        # Arrange
        request = CreateDeploymentRequest(
            deployment_name="internal", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", access_mode="internal",
        )
        schedule = Mock()
        with (
            patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "*.models.example.com"}),
            patch.object(deployment, "resolve_profile_request", return_value=request),
            patch.object(deployment, "upsert_deployed_instance", return_value=SimpleNamespace(id=1)),
        ):
            # Act
            result = deployment.create_deployment(request, self.session, {"role": "member"}, schedule)
        # Assert
        self.assertIsNone(result.external_url)
        schedule.assert_called_once()
        self.assertIsNone(schedule.call_args.args[2].ingress_host)
