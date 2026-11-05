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
