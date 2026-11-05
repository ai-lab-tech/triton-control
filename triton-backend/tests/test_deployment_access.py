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
                "TRITON_DEPLOY_INGRESS_CLASS": "traefik",
                "TRITON_DEPLOY_INGRESS_TLS_SECRET": "",
                "TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER": "",
                "TRITON_DEPLOY_INGRESS_HTTP_ENTRYPOINT": "web",
                "TRITON_DEPLOY_INGRESS_HTTPS_ENTRYPOINT": "websecure",
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
        self.net, self.core, self.apps, self.custom = Mock(), Mock(), Mock(), Mock()
        self.core.read_namespaced_secret.return_value = client.V1Secret(
            metadata=client.V1ObjectMeta(name="new-auth", uid="secret-uid", labels={access.OWNER_LABEL: "model"}),
        )
        self.net.read_ingress_class.return_value.spec.controller = "traefik.io/ingress-controller"
        self.apps.read_namespaced_deployment.return_value.metadata.uid = "deployment-uid"
        self.net.read_namespaced_ingress.side_effect = ApiException(status=404)
        self.net.list_ingress_for_all_namespaces.return_value.items = []
        self.net.create_namespaced_ingress.side_effect = lambda ns, body: ingress_object(body)
        self.net.replace_namespaced_ingress.side_effect = lambda name, ns, body: ingress_object(body)
        for target, value in [
            ("api_client", Mock()),
            ("client.NetworkingV1Api", Mock(return_value=self.net)),
            ("client.CoreV1Api", Mock(return_value=self.core)),
            ("client.AppsV1Api", Mock(return_value=self.apps)),
            ("client.CustomObjectsApi", Mock(return_value=self.custom)),
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
            metadata=client.V1ObjectMeta(name="old-auth", uid="old-uid", labels={access.OWNER_LABEL: "model"}),
            data={"users": base64.b64encode(b"user:hash").decode()},
        )
        self.custom.get_namespaced_custom_object.return_value = access.access_manifests(
            request, "control", "model", "model-service", auth_secret="old-auth" if authenticated else None,
        )[-2] if authenticated else {}
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
        username, hashed = secret["stringData"]["users"].strip().split(":", 1)
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
            ingress["metadata"]["annotations"][access.AUTH_SECRET_ANNOTATION], secret["metadata"]["name"]
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
        self.assertNotIn(access.AUTH_SECRET_ANNOTATION, annotations)
        self.assertNotIn(access.MIDDLEWARE_ANNOTATION, annotations)
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
        self.net.list_ingress_for_all_namespaces.return_value.items = [other]
        with self.assertRaises(ConflictError):
            self.update(self.request(basic_auth_enabled=True, basic_auth_username="bob", basic_auth_password="new"))
        self.core.create_namespaced_secret.assert_not_called()

    def test_creation_preflight_conflict_does_not_create_database_record(self):
        from app.services.deployment import deployment
        other = ingress_object(access.access_manifests(self.request(), "other", "model", "svc")[-1])
        self.net.list_ingress_for_all_namespaces.return_value.items = [other]
        request = CreateDeploymentRequest(
            deployment_name="model", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", ingress_host="model.example.com",
        )
        with (
            patch.object(deployment, "resolve_profile_request", return_value=request),
            patch.object(deployment, "upsert_deployed_instance") as record,
            self.assertRaises(ConflictError),
        ):
            deployment.create_deployment(request, Mock(), {"role": "member"})
        record.assert_not_called()

    def test_creation_rechecks_hostname_before_creating_resources(self):
        other = ingress_object(access.access_manifests(self.request(), "other", "model", "svc")[-1])
        self.net.list_ingress_for_all_namespaces.return_value.items = [other]
        request = CreateDeploymentRequest(
            deployment_name="model", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", ingress_host="model.example.com",
            basic_auth_enabled=True, basic_auth_username="user", basic_auth_password="secret",
        )
        with (
            patch.object(k8s, "_client", return_value=Mock()),
            patch.object(k8s, "_ensure_namespace"),
            patch.object(k8s, "record_deployment_failure") as failed,
            patch("kubernetes.utils.create_from_dict") as create,
        ):
            k8s.apply_deployment_resources(1, request, "control", "model", "model-service", "s3", "triton:latest")
        create.assert_not_called()
        self.custom.create_namespaced_custom_object.assert_not_called()
        self.assertIn("hostname overlaps", failed.call_args.args[1])

    def test_missing_central_tls_secret_rejected_before_ingress_creation(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_TLS_SECRET": "missing"}):
            self.core.read_namespaced_secret.side_effect = ApiException(status=404)
            with self.assertRaises(BadRequestError):
                self.update(self.request())
        self.net.create_namespaced_ingress.assert_not_called()

    def test_auth_cannot_be_enabled_without_credentials(self):
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))

    def test_users_cannot_select_ingress_class(self):
        for ingress_class in ["nginx", "traefik", "custom"]:
            with self.subTest(ingress_class=ingress_class), self.assertRaises(ValidationError):
                self.request(ingress_class_name=ingress_class)
        self.net.create_namespaced_ingress.assert_not_called()

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
            ingress_host="model.example.com", ingress_scheme="https",
            basic_auth_enabled=True,
            basic_auth_username="user",
            basic_auth_password="password",
            allow_metrics=False,
        )
        manifests = k8s._manifests(request, "control", "model", "model-service", "model-s3", "triton:latest")
        ingress = manifests[-1]
        self.assertEqual(ingress["spec"]["tls"], [{"hosts": ["model.example.com"]}])
        self.assertEqual(len(ingress["spec"]["rules"][0]["http"]["paths"]), 1)
        self.assertEqual(manifests[-2]["kind"], "Middleware")
        self.assertEqual(manifests[-3]["kind"], "Secret")
        self.assertNotIn("password", repr(request))

    def test_unauthenticated_ingress_also_requires_traefik(self):
        self.net.read_ingress_class.return_value.spec.controller = "k8s.io/ingress-nginx"
        with self.assertRaises(BadRequestError):
            self.update(self.request())
        self.net.create_namespaced_ingress.assert_not_called()

    def test_custom_admin_class_and_entrypoints(self):
        with patch.dict(os.environ, {
            "TRITON_DEPLOY_INGRESS_CLASS": "public-traefik",
            "TRITON_DEPLOY_INGRESS_HTTPS_ENTRYPOINT": "secure",
        }):
            self.update(self.request())
        self.net.read_ingress_class.assert_called_once_with("public-traefik")
        body = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(body["spec"]["ingressClassName"], "public-traefik")
        self.assertEqual(body["metadata"]["annotations"][f"{access.AUTH_PREFIX}router.entrypoints"], "secure")
        self.assertEqual(body["metadata"]["annotations"][f"{access.AUTH_PREFIX}router.tls"], "true")

    def test_disabled_ingress_rejects_edit_before_kubernetes(self):
        with (
            patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}),
            self.assertRaisesRegex(BadRequestError, "Model Ingress is disabled"),
        ):
            self.update(self.request())
        self.net.read_namespaced_ingress.assert_not_called()
        self.net.create_namespaced_ingress.assert_not_called()

    def test_disabled_ingress_still_allows_removing_existing_endpoint(self):
        self.existing()
        with patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}):
            response = self.update(DeploymentAccessRequest(access_mode="internal"))
        self.assertEqual(response.access_mode, "internal")
        self.net.delete_namespaced_ingress.assert_called_once()

    def test_queued_ingress_rechecks_switch_before_kubernetes(self):
        request = CreateDeploymentRequest(
            deployment_name="model", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", ingress_host="model.example.com",
        )
        with (
            patch.dict(os.environ, {"TRITON_DEPLOY_INGRESS_ENABLED": "false"}),
            patch.object(k8s, "_client") as kube,
            patch.object(k8s, "record_deployment_failure") as failed,
        ):
            k8s.apply_deployment_resources(1, request, "control", "model", "model-service", "s3", "triton:latest")
        kube.assert_not_called()
        self.assertIn("Model Ingress is disabled", failed.call_args.args[1])

    def test_http_policy_rejects_edit_before_mutations(self):
        with (
            patch("app.repositories.deployment_policy.get_override", return_value=False),
            self.assertRaisesRegex(BadRequestError, "HTTP endpoints are disabled"),
        ):
            self.update(self.request(ingress_scheme="http"))
        self.net.create_namespaced_ingress.assert_not_called()
        self.net.replace_namespaced_ingress.assert_not_called()
        self.core.create_namespaced_secret.assert_not_called()

    def test_http_policy_preserves_existing_endpoint(self):
        previous = ingress_object(access.access_manifests(
            self.request(ingress_scheme="http"), "control", "model", "model-service",
        )[-1])
        self.net.read_namespaced_ingress.side_effect = None
        self.net.read_namespaced_ingress.return_value = previous
        with patch("app.repositories.deployment_policy.get_override", return_value=False):
            result = self.update(self.request(ingress_scheme="http"))
        self.assertEqual(result.ingress_scheme, "http")

    def test_queued_http_deployment_rechecks_policy_before_resources(self):
        request = CreateDeploymentRequest(
            deployment_name="model", image="triton:latest", s3_url="s3://bucket",
            s3_access_key="key", s3_secret_key="secret", ingress_host="model.example.com", ingress_scheme="http",
        )
        with (
            patch.object(k8s, "session_factory") as sessions,
            patch.object(k8s, "_client") as kube,
            patch.object(k8s, "record_deployment_failure") as failed,
            patch("app.repositories.deployment_policy.get_override", return_value=False),
        ):
            k8s.apply_deployment_resources(1, request, "control", "model", "model-service", "s3", "triton:latest")
        sessions.assert_called_once()
        kube.assert_not_called()
        self.assertIn("HTTP endpoints are disabled", failed.call_args.args[1])

    def test_http_does_not_configure_tls_or_issuer(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER": "issuer"}):
            with patch("app.repositories.deployment_policy.get_override", return_value=True):
                self.update(self.request(ingress_scheme="http"))
        body = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertNotIn("tls", body["spec"])
        self.assertEqual(body["metadata"]["annotations"], {f"{access.AUTH_PREFIX}router.entrypoints": "web"})

    def test_cert_manager_uses_unique_secret_and_admin_issuer(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER": "issuer"}):
            self.update(self.request())
        body = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(body["spec"]["tls"][0]["secretName"], "model-ingress-tls")
        self.assertEqual(body["metadata"]["annotations"]["cert-manager.io/cluster-issuer"], "issuer")
        self.core.read_namespaced_secret.assert_not_called()

    def test_conflicting_certificate_configuration_rejected(self):
        with patch.dict(os.environ, {
            "TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER": "issuer", "TRITON_DEPLOY_INGRESS_TLS_SECRET": "existing",
        }), self.assertRaises(BadRequestError):
            self.update(self.request())
        self.net.create_namespaced_ingress.assert_not_called()

    def test_middleware_uses_hashed_secret_and_is_garbage_collected_with_it(self):
        self.update(self.request(basic_auth_enabled=True, basic_auth_username="user", basic_auth_password="secret"))
        secret = self.core.create_namespaced_secret.call_args.args[1]
        middleware = self.custom.create_namespaced_custom_object.call_args.args[-1]
        self.assertEqual(middleware["spec"]["basicAuth"]["secret"], secret["metadata"]["name"])
        self.assertTrue(middleware["spec"]["basicAuth"]["removeHeader"])
        self.assertEqual(middleware["metadata"]["ownerReferences"][0]["kind"], "Secret")
        self.assertEqual(middleware["metadata"]["ownerReferences"][0]["uid"], "secret-uid")
        ingress = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(ingress["metadata"]["annotations"][access.MIDDLEWARE_ANNOTATION],
                         f"control-{secret['metadata']['name']}@kubernetescrd")

    def test_missing_crd_cleans_new_secret_and_keeps_old_ingress(self):
        self.existing(authenticated=True)
        self.custom.create_namespaced_custom_object.side_effect = ApiException(status=404)
        with self.assertRaises(BadGatewayError):
            self.update(self.request(basic_auth_enabled=True, basic_auth_username="user", basic_auth_password="secret"))
        self.net.replace_namespaced_ingress.assert_not_called()
        new_secret = self.core.create_namespaced_secret.call_args.args[1]["metadata"]["name"]
        self.core.delete_namespaced_secret.assert_called_once_with(new_secret, "control")

    def test_missing_retained_middleware_requires_new_credentials(self):
        self.existing(authenticated=True)
        self.custom.get_namespaced_custom_object.side_effect = ApiException(status=404)
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))
        self.net.replace_namespaced_ingress.assert_not_called()
        self.core.delete_namespaced_secret.assert_not_called()

    def test_modified_retained_middleware_is_not_used(self):
        self.existing(authenticated=True)
        self.custom.get_namespaced_custom_object.return_value["spec"]["basicAuth"]["secret"] = "someone-else"
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))
        self.net.replace_namespaced_ingress.assert_not_called()

    def test_legacy_nginx_auth_is_reported_and_requires_credentials_to_migrate(self):
        ingress = self.existing()
        ingress.metadata.annotations.update({
            "nginx.ingress.kubernetes.io/auth-type": "basic",
            "nginx.ingress.kubernetes.io/auth-secret": "old-auth",
        })
        self.core.read_namespaced_secret.return_value.data = {"auth": "old-hash"}
        self.assertTrue(access.get_access(Mock(), {"role": "member"}, 7).basic_auth_enabled)
        with self.assertRaises(BadRequestError):
            self.update(self.request(basic_auth_enabled=True))
        self.net.replace_namespaced_ingress.assert_not_called()
        self.update(self.request(basic_auth_enabled=True, basic_auth_username="user", basic_auth_password="secret"))
        annotations = self.net.replace_namespaced_ingress.call_args.args[2]["metadata"]["annotations"]
        self.assertNotIn("nginx.ingress.kubernetes.io/auth-type", annotations)
        self.assertIn(access.MIDDLEWARE_ANNOTATION, annotations)
        self.core.delete_namespaced_secret.assert_called_once_with("old-auth", "control")

    def test_initial_deployment_creates_middleware_before_ingress_via_custom_api(self):
        request = CreateDeploymentRequest(
            deployment_name="model", image="triton:latest",
            s3_url="s3://bucket", s3_access_key="key", s3_secret_key="secret",
            ingress_host="model.example.com", basic_auth_enabled=True,
            basic_auth_username="user", basic_auth_password="secret",
        )
        events = []
        self.custom.create_namespaced_custom_object.side_effect = lambda *args: events.append("Middleware")
        with (
            patch.object(k8s, "_client", return_value=Mock()),
            patch.object(k8s, "_ensure_namespace"),
            patch.object(k8s, "gpu_runtime_class_name", return_value=None),
            patch.object(k8s, "_deployment_urls", return_value={"http": "internal", "metrics": "metrics"}),
            patch.object(k8s, "update_instance_after_apply") as applied,
            patch.object(k8s, "record_deployment_failure") as failed,
            patch("kubernetes.utils.create_from_dict",
                  side_effect=lambda *args, **kw: events.append(kw["data"]["kind"])),
        ):
            k8s.apply_deployment_resources(1, request, "control", "model", "model-service", "model-s3", "triton:latest")
        failed.assert_not_called()
        applied.assert_called_once()
        self.assertEqual(events[-3:], ["Secret", "Middleware", "Ingress"])
        self.assertEqual(self.custom.create_namespaced_custom_object.call_args.args[:4],
                         ("traefik.io", "v1alpha1", "control", "middlewares"))

    def test_short_hostname_with_separate_protocol(self):
        request = DeploymentAccessRequest(ingress_host="smollm", ingress_scheme="https")
        self.assertEqual(request.ingress_host, "smollm")
        self.assertEqual(request.ingress_scheme, "https")

    def test_base_domain_applies_to_ingress_tls_and_response(self):
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            response = self.update(self.request(ingress_host="smollm"))
        manifest = self.net.create_namespaced_ingress.call_args.args[1]
        self.assertEqual(manifest["spec"]["rules"][0]["host"], "smollm.models.example.com")
        self.assertEqual(manifest["spec"]["tls"][0]["hosts"], ["smollm.models.example.com"])
        self.assertEqual(response.url, "https://smollm.models.example.com")
        self.assertEqual(response.ingress_host, "smollm.models.example.com")
        self.assertEqual(response.hostname, "smollm")

    def test_base_domain_conflict_checks_resolved_hostname(self):
        other = ingress_object(access.access_manifests(
            self.request(ingress_host="smollm.models.example.com"), "other", "other", "other-service",
        )[-1])
        self.net.list_ingress_for_all_namespaces.return_value.items = [other]
        with patch.dict(os.environ, {"TRITON_DEPLOY_BASE_DOMAIN": "models.example.com"}):
            with self.assertRaises(ConflictError):
                self.update(self.request(ingress_host="smollm"))
        self.net.create_namespaced_ingress.assert_not_called()

    def test_invalid_inputs(self):
        for values in [
            {"access_mode": "ingress"},
            {"access_mode": "internal", "ingress_host": "example.com"},
            {"ingress_host": "bad..example.com"},
            {"ingress_host": "https://smollm"},
            {"ingress_host": "http://smollm"},
            {"ingress_host": "smollm:443"},
            {"ingress_host": "smollm/path"},
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
