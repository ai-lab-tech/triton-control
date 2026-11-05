"""Exposure lifecycle, credential handling and access-control regression tests."""

import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bcrypt
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from pydantic import ValidationError

from app.exceptions import BadGatewayError, BadRequestError, ConflictError, ForbiddenError
from app.schemas import CreateDeploymentRequest
from app.schemas.deployment_access import DeploymentAccessRequest
from app.services.deployment import access
from app.services.deployment import kubernetes as k8s


def ingress_object(manifest):
    return client.ApiClient()._ApiClient__deserialize(manifest, "V1Ingress")


class DeploymentAccessTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "TRITON_DEPLOY_INGRESS_CLASS": "nginx",
                "TRITON_DEPLOY_INGRESS_TLS_SECRET": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.instance = SimpleNamespace(
            is_self_deployed=True,
            deployment_namespace="control",
            deployment_name="model",
            deployment_service_name="model-service",
            url="https://old.example.com",
            metrics_url="https://old.example.com/metrics",
        )
        self.net, self.core, self.apps = Mock(), Mock(), Mock()
        self.net.read_ingress_class.return_value.spec.controller = "k8s.io/ingress-nginx"
        self.apps.read_namespaced_deployment.return_value.metadata.uid = "deployment-uid"
        self.net.read_namespaced_ingress.side_effect = ApiException(status=404)
        self.net.list_namespaced_ingress.return_value.items = []
        self.net.create_namespaced_ingress.side_effect = lambda ns, body: ingress_object(body)
        self.net.replace_namespaced_ingress.side_effect = lambda name, ns, body: ingress_object(body)
        for target, value in [
            ("api_client", Mock()),
            ("client.NetworkingV1Api", Mock(return_value=self.net)),
            ("client.CoreV1Api", Mock(return_value=self.core)),
            ("client.AppsV1Api", Mock(return_value=self.apps)),
            ("get_instance_or_404", Mock(return_value=self.instance)),
            ("instances.save", Mock()),
        ]:
            # api_client is a context manager; use a separate MagicMock for it.
            replacement = (
                patch.object(access, target, return_value=client.ApiClient())
                if target == "api_client"
                else (patch(f"app.services.deployment.access.{target}", value))
            )
            replacement.start()
            self.addCleanup(replacement.stop)

    def request(self, **overrides):
        return DeploymentAccessRequest(
            **{
                "access_mode": "ingress",
                "ingress_host": "model.example.com",
                **overrides,
            }
        )

    def update(self, request):
        return access.update_access(request, Mock(), {"role": "member"}, 7)

    def existing(self, *, authenticated=False):
        request = self.request(basic_auth_enabled=authenticated)
        manifest = access.access_manifests(
            request,
            "control",
            "model",
            "model-service",
            auth_secret="old-auth" if authenticated else None,
        )[-1]
        manifest["metadata"]["resourceVersion"] = "42"
        manifest["metadata"]["labels"]["example.com/label"] = "keep"
        manifest["metadata"]["annotations"]["example.com/custom"] = "keep"
        ingress = ingress_object(manifest)
        self.net.read_namespaced_ingress.side_effect = None
        self.net.read_namespaced_ingress.return_value = ingress
        self.core.read_namespaced_secret.return_value = client.V1Secret(
            metadata=client.V1ObjectMeta(labels={access.OWNER_LABEL: "model"}),
            data={"auth": base64.b64encode(b"user:hash").decode()},
        )
        return ingress

    def test_internal_to_ingress_uses_central_tls_and_keeps_management_internal(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_TLS_SECRET": "central-cert"}):
            self.core.read_namespaced_secret.return_value = client.V1Secret(
                type="kubernetes.io/tls",
                data={"tls.crt": "cert", "tls.key": "key"},
            )
            response = self.update(self.request())
        body = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(body["spec"]["tls"], [{"hosts": ["model.example.com"], "secretName": "central-cert"}])
        self.assertEqual(response.url, "https://model.example.com")
        self.assertEqual(self.instance.url, "http://model-service.control.svc.cluster.local:18000")
        self.assertEqual(self.instance.metrics_url, "http://model-service.control.svc.cluster.local:18002/metrics")
        self.apps.patch_namespaced_deployment.assert_not_called()
        self.apps.replace_namespaced_deployment.assert_not_called()
        self.core.patch_namespaced_service.assert_not_called()

    def test_ingress_to_internal_deletes_ingress_then_owned_secret(self):
        self.existing(authenticated=True)
        events = Mock()
        events.attach_mock(self.net.delete_namespaced_ingress, "ingress")
        events.attach_mock(self.core.delete_namespaced_secret, "secret")
        response = self.update(DeploymentAccessRequest(access_mode="internal"))
        self.assertEqual(response.access_mode, "internal")
        self.assertEqual([call[0] for call in events.mock_calls], ["ingress", "secret"])
        self.core.delete_namespaced_secret.assert_called_once_with("old-auth", "control")

    def test_password_is_bcrypt_htpasswd_and_never_returned(self):
        response = self.update(
            self.request(
                basic_auth_enabled=True,
                basic_auth_username="alice",
                basic_auth_password="my password",
            )
        )
        secret = self.core.create_namespaced_secret.call_args.args[1]
        username, hashed = secret["stringData"]["auth"].strip().split(":", 1)
        self.assertEqual(username, "alice")
        self.assertTrue(bcrypt.checkpw(b"my password", hashed.encode()))
        self.assertFalse(bcrypt.checkpw(b"wrong", hashed.encode()))
        self.assertNotIn("my password", repr(secret))
        self.assertNotIn("password", response.model_dump_json())
        self.assertNotIn("alice", response.model_dump_json())
        self.assertTrue(response.basic_auth_enabled)
        self.assertEqual(secret["metadata"]["ownerReferences"][0]["uid"], "deployment-uid")
        ingress = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(
            ingress["metadata"]["annotations"][f"{access.AUTH_PREFIX}auth-secret"], secret["metadata"]["name"]
        )

    def test_blank_credentials_keep_existing_secret(self):
        self.existing(authenticated=True)
        response = self.update(self.request(basic_auth_enabled=True))
        self.assertTrue(response.basic_auth_enabled)
        self.core.create_namespaced_secret.assert_not_called()
        self.core.delete_namespaced_secret.assert_not_called()
        body = self.net.replace_namespaced_ingress.call_args.args[2]
        self.assertEqual(body["metadata"]["resourceVersion"], "42")
        self.assertEqual(body["metadata"]["labels"]["example.com/label"], "keep")
        self.assertEqual(body["metadata"]["annotations"]["example.com/custom"], "keep")

    def test_rotation_creates_new_secret_before_replacing_ingress(self):
        self.existing(authenticated=True)
        events = Mock()
        events.attach_mock(self.core.create_namespaced_secret, "create_secret")
        events.attach_mock(self.net.replace_namespaced_ingress, "replace_ingress")
        events.attach_mock(self.core.delete_namespaced_secret, "delete_secret")
        self.update(self.request(basic_auth_enabled=True, basic_auth_username="bob", basic_auth_password="new"))
        self.assertEqual([call[0] for call in events.mock_calls], ["create_secret", "replace_ingress", "delete_secret"])
        self.core.delete_namespaced_secret.assert_called_once_with("old-auth", "control")

    def test_disabling_auth_removes_annotations_and_secret(self):
        self.existing(authenticated=True)
        response = self.update(self.request())
        self.assertFalse(response.basic_auth_enabled)
        annotations = self.net.replace_namespaced_ingress.call_args.args[2]["metadata"]["annotations"]
        self.assertNotIn(f"{access.AUTH_PREFIX}auth-secret", annotations)
        self.assertNotIn(f"{access.AUTH_PREFIX}auth-type", annotations)
        self.core.delete_namespaced_secret.assert_called_once_with("old-auth", "control")

    def test_unowned_secret_is_never_deleted_or_reused(self):
        self.existing(authenticated=True)
        self.core.read_namespaced_secret.return_value.metadata.labels = {}
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))
        self.update(DeploymentAccessRequest(access_mode="internal"))
        self.core.delete_namespaced_secret.assert_not_called()

    def test_replacement_conflict_does_not_delete_old_credentials(self):
        self.existing(authenticated=True)
        self.net.replace_namespaced_ingress.side_effect = ApiException(status=409)
        with self.assertRaises(ConflictError):
            self.update(self.request(basic_auth_enabled=True, basic_auth_username="bob", basic_auth_password="new"))
        new_secret = self.core.create_namespaced_secret.call_args.args[1]["metadata"]["name"]
        self.core.delete_namespaced_secret.assert_called_once_with(new_secret, "control")
        access.instances.save.assert_not_called()

    def test_ingress_delete_failure_keeps_credentials_and_database(self):
        self.existing(authenticated=True)
        error = ApiException(status=500)
        error.body = "sensitive error"
        self.net.delete_namespaced_ingress.side_effect = error
        with self.assertRaises(BadGatewayError) as raised:
            self.update(DeploymentAccessRequest(access_mode="internal"))
        self.assertNotIn("sensitive", str(raised.exception))
        self.core.delete_namespaced_secret.assert_not_called()
        access.instances.save.assert_not_called()

    def test_read_only_user_cannot_write(self):
        with self.assertRaises(ForbiddenError):
            access.update_access(self.request(), Mock(), {"role": "viewer"}, 7)
        access.get_instance_or_404.assert_not_called()
        self.net.create_namespaced_ingress.assert_not_called()

    def test_unassigned_user_cannot_read_or_update(self):
        access.get_instance_or_404.side_effect = ForbiddenError("Instance access denied")
        with self.assertRaises(ForbiddenError):
            self.update(self.request())
        with self.assertRaises(ForbiddenError):
            access.get_access(Mock(), {"role": "member"}, 7)
        self.net.read_namespaced_ingress.assert_not_called()

    def test_external_instance_rejected(self):
        self.instance.is_self_deployed = False
        with self.assertRaises(BadRequestError):
            self.update(self.request())

    def test_duplicate_host_rejected_before_secret_creation(self):
        other = ingress_object(access.access_manifests(self.request(), "control", "other", "other-service")[-1])
        self.net.list_namespaced_ingress.return_value.items = [other]
        with self.assertRaises(ConflictError):
            self.update(self.request(basic_auth_enabled=True, basic_auth_username="bob", basic_auth_password="new"))
        self.core.create_namespaced_secret.assert_not_called()

    def test_missing_central_tls_secret_rejected_before_ingress_creation(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_TLS_SECRET": "missing"}):
            self.core.read_namespaced_secret.side_effect = ApiException(status=404)
            with self.assertRaises(BadRequestError):
                self.update(self.request())
        self.net.create_namespaced_ingress.assert_not_called()

    def test_auth_cannot_be_enabled_without_credentials(self):
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))

    def test_auth_rejects_unsupported_controller(self):
        with self.assertRaises(BadRequestError):
            self.update(self.request(ingress_class_name="traefik", basic_auth_enabled=True))

    def test_auth_checks_actual_ingress_controller(self):
        self.net.read_ingress_class.return_value.spec.controller = "example.com/other-controller"
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True, basic_auth_username="user", basic_auth_password="secret"))
        self.core.create_namespaced_secret.assert_not_called()
        self.net.create_namespaced_ingress.assert_not_called()

    def test_internal_repeated_save_is_idempotent(self):
        response = self.update(DeploymentAccessRequest(access_mode="internal"))
        self.assertEqual(response.access_mode, "internal")
        self.net.delete_namespaced_ingress.assert_not_called()

    def test_manifests_default_certificate_metrics_and_create_integration(self):
        request = CreateDeploymentRequest(
            deployment_name="model",
            image="triton:latest",
            s3_url="s3://bucket",
            s3_access_key="key",
            s3_secret_key="secret",
            ingress_host="https://model.example.com/",
            basic_auth_enabled=True,
            basic_auth_username="user",
            basic_auth_password="password",
            allow_metrics=False,
        )
        manifests = k8s._manifests(request, "control", "model", "model-service", "model-s3", "triton:latest")
        ingress = manifests[-1]
        self.assertEqual(ingress["spec"]["tls"], [{"hosts": ["model.example.com"]}])
        self.assertEqual(len(ingress["spec"]["rules"][0]["http"]["paths"]), 1)
        self.assertEqual(manifests[-2]["kind"], "Secret")
        self.assertNotIn("password", repr(request))

    def test_invalid_inputs(self):
        for values in [
            {"access_mode": "ingress"},
            {"access_mode": "internal", "ingress_host": "example.com"},
            {"ingress_host": "bad..example.com"},
            {"ingress_host": "https://user:password@example.com"},
            {"ingress_host": "https://example.com/path"},
            {"ingress_host": "example.com", "ingress_scheme": "http", "basic_auth_enabled": True},
            {"ingress_host": "example.com", "basic_auth_enabled": True, "basic_auth_username": "bad:user"},
            {"ingress_host": "example.com", "basic_auth_enabled": True, "basic_auth_password": "x" * 73},
            {"ingress_host": "example.com", "basic_auth_username": "user"},
        ]:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                DeploymentAccessRequest(**values)


if __name__ == "__main__":
    unittest.main()
