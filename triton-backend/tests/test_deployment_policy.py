"""Deployment HTTP policy precedence, authorization and enforcement."""

import os
import unittest
from unittest.mock import patch

from sqlmodel import Session, SQLModel, create_engine

from app.exceptions import BadRequestError, ForbiddenError
from app.schemas.deployment_access import DeploymentAccessRequest
from app.schemas.deployment_policy import DeploymentPolicyUpdate
from app.services.deployment.policy import get_policy, update_policy, validate_endpoint_policy


class DeploymentPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        env = patch.dict(os.environ, {"TRITON_DEPLOY_ALLOW_HTTP": "false", "TRITON_DEPLOY_INGRESS_ENABLED": "true"})
        env.start()
        self.addCleanup(env.stop)

    def http(self, host="model.example.com"):
        return DeploymentAccessRequest(ingress_host=host, ingress_scheme="http")

    def test_default_is_https_only(self):
        self.assertFalse(get_policy(self.session).allow_http)
        with self.assertRaises(BadRequestError):
            validate_endpoint_policy(
                DeploymentAccessRequest(ingress_host="model.example.com", ingress_scheme="http"), self.session,
            )

    def test_helm_default_admin_override_and_reset(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_ALLOW_HTTP": "true"}):
            self.assertTrue(get_policy(self.session).allow_http)
            result = update_policy(DeploymentPolicyUpdate(allow_http=False), self.session, {"role": "admin"})
            self.assertFalse(result.allow_http)
            self.assertTrue(result.default_allow_http)
            self.assertEqual(result.source, "admin")
            result = update_policy(DeploymentPolicyUpdate(allow_http=None), self.session, {"role": "admin"})
            self.assertTrue(result.allow_http)
            self.assertEqual(result.source, "environment")

    def test_admin_can_enable_http_and_override_survives_new_session(self):
        update_policy(DeploymentPolicyUpdate(allow_http=True), self.session, {"role": "admin"})
        with Session(self.engine) as other:
            validate_endpoint_policy(self.http(), other)
            self.assertTrue(get_policy(other).allow_http)

    def test_non_admin_cannot_update_policy(self):
        for role in ("member", "viewer"):
            with self.subTest(role=role), self.assertRaises(ForbiddenError):
                update_policy(DeploymentPolicyUpdate(allow_http=True), self.session, {"role": role})
        self.assertFalse(get_policy(self.session).allow_http)

    def test_existing_http_is_preserved_but_hostname_change_is_blocked(self):
        validate_endpoint_policy(self.http(), self.session, existing_host="model.example.com", existing_scheme="http")
        with self.assertRaises(BadRequestError):
            validate_endpoint_policy(self.http("new.example.com"), self.session,
                          existing_host="model.example.com", existing_scheme="http")
        with self.assertRaises(BadRequestError):
            validate_endpoint_policy(
                self.http(), self.session, existing_host="model.example.com", existing_scheme="https",
            )

    def test_internal_and_https_remain_allowed(self):
        validate_endpoint_policy(DeploymentAccessRequest(access_mode="internal"), self.session)
        validate_endpoint_policy(DeploymentAccessRequest(ingress_host="model.example.com"), self.session)

    def test_ingress_switch_blocks_http_https_and_cannot_be_overridden_in_ui(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}):
            update_policy(DeploymentPolicyUpdate(allow_http=True), self.session, {"role": "admin"})
            self.assertFalse(get_policy(self.session).ingress_enabled)
            for scheme in ("http", "https"):
                with self.subTest(scheme=scheme), self.assertRaisesRegex(BadRequestError, "Model Ingress is disabled"):
                    validate_endpoint_policy(
                        DeploymentAccessRequest(ingress_host="model.example.com", ingress_scheme=scheme),
                        self.session, existing_host="model.example.com", existing_scheme=scheme,
                    )
            validate_endpoint_policy(DeploymentAccessRequest(access_mode="internal"), self.session)

    def test_base_domain_resolves_short_hostname_without_mutating_input(self):
        from app.services.deployment.policy import resolve_hostname
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            request = DeploymentAccessRequest(ingress_host="smollm", ingress_scheme="https")
            resolved = resolve_hostname(request)
            self.assertEqual(resolved.ingress_host, "smollm.models.example.com")
            self.assertEqual(request.ingress_host, "smollm")
            self.assertEqual(get_policy(self.session).base_domain, "models.example.com")
            with self.assertRaises(BadRequestError):
                resolve_hostname(DeploymentAccessRequest(ingress_host="smollm.other.de"))

    def test_existing_full_host_is_preserved_and_not_appended_twice(self):
        from app.services.deployment.policy import resolve_hostname, short_hostname
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            request = DeploymentAccessRequest(ingress_host="smollm.models.example.com")
            result = resolve_hostname(request, existing_host=request.ingress_host)
            self.assertEqual(result.ingress_host, request.ingress_host)
            self.assertEqual(short_hostname(request.ingress_host), "smollm")
            self.assertEqual(short_hostname("legacy.other.de"), "legacy.other.de")

    def test_empty_base_domain_preserves_standalone_host_and_invalid_domain_is_rejected(self):
        from app.services.deployment.policy import resolve_hostname
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": ""}):
            self.assertEqual(resolve_hostname(self.http("smollm")).ingress_host, "smollm")
        for domain in ("*.models.example.com", "https://models.example.com", "models.example.com:443", "bad..domain"):
            with self.subTest(domain=domain), patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": domain}):
                with self.assertRaises(BadRequestError):
                    resolve_hostname(self.http("smollm"))

    def test_creation_schedules_resolved_hostname_once_and_returns_full_url(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        from app.schemas import CreateDeploymentRequest
        from app.services.deployment import deployment
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
            result = deployment.create_deployment(request, self.session, {"role": "member"}, schedule)
        self.assertEqual(result.external_url, "https://smollm.models.example.com")
        self.assertEqual(validate_host.call_args.args[0].ingress_host, "smollm.models.example.com")
        self.assertEqual(schedule.call_args.args[2].ingress_host, "smollm.models.example.com")
        self.assertEqual(request.ingress_host, "smollm")
