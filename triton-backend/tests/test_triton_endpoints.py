"""SSRF policy regression tests using Arrange/Act/Assert."""

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from kubernetes import client  # type: ignore[import-untyped]

from app.db.entities import TritonInstanceEntity
from app.exceptions import BadRequestError
from app.schemas import CreateTritonInstanceRequest
from app.services.triton import endpoints, instances
from app.services.triton.client import TritonService


class ExternalEndpointTests(unittest.TestCase):
    def test_exact_host_and_port_are_required(self):
        # Arrange
        rejected = [
            "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8002/metrics",
            "http://other.control.svc.cluster.local:8002/metrics", "http://approved.example:8000/metrics",
            "http://approved.example.evil:8002/metrics", "http://approved.example:8002@evil.example/metrics",
            "http://evil.example/approved.example:8002", "http://approved.example:0/metrics",
        ]
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "approved.example:8002"}):
            # Act / Assert
            for url in rejected:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    endpoints.approved_external_url(url)
            self.assertEqual(endpoints.approved_external_url("http://APPROVED.example:8002/metrics"),
                             "http://approved.example:8002/metrics")

    def test_empty_allowlist_denies_external_endpoints(self):
        # Arrange
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": ""}):
            # Act / Assert
            with self.assertRaisesRegex(ValueError, "not approved"):
                endpoints.approved_external_url("https://triton.example:8000")

    def test_credentials_protocols_and_ambiguous_urls_are_rejected(self):
        # Arrange
        rejected = [
            "ftp://approved.example:8002/metrics", "http://user:password@approved.example:8002/metrics",
            "http://approved.example:8002/metrics#fragment", "http://approved.example:8002\\@evil/metrics",
            "http://approved.example:8002/metrics\n", "http://%61pproved.example:8002/metrics",
            "http://approved.example:bad/metrics",
        ]
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "approved.example:8002"}):
            # Act / Assert
            for url in rejected:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    endpoints.approved_external_url(url)

    def test_explicit_ipv6_and_default_ports_are_supported(self):
        # Arrange
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "[2001:db8::1]:8002,approved.example:443"}):
            # Act
            ipv6 = endpoints.approved_external_url("http://[2001:db8::1]:8002/metrics")
            tls = endpoints.approved_external_url("https://approved.example/metrics")

        # Assert
        self.assertEqual(ipv6, "http://[2001:db8::1]:8002/metrics")
        self.assertEqual(tls, "https://approved.example/metrics")

    def test_malformed_operator_configuration_fails_closed(self):
        # Arrange
        entries = ["*", "approved.example", "approved.example:8002/path", "user@approved.example:8002"]

        # Act / Assert
        for entry in entries:
            with self.subTest(entry=entry), patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": entry}):
                with self.assertRaises(ValueError):
                    endpoints.approved_external_url("http://approved.example:8002/metrics")


class ManagedEndpointTests(unittest.TestCase):
    def setUp(self):
        selector = {"app": "triton", "deployment": "iris"}
        self.service = client.V1Service(spec=client.V1ServiceSpec(
            type="ClusterIP", selector=selector,
            ports=[client.V1ServicePort(name="http", port=18000), client.V1ServicePort(name="metrics", port=18002)],
        ))
        self.deployment = client.V1Deployment(spec=client.V1DeploymentSpec(
            selector=client.V1LabelSelector(match_labels=selector),
            template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=[client.V1Container(name="triton")])),
        ))
        self.api = patch.object(endpoints, "api_client")
        self.api.start()
        self.addCleanup(self.api.stop)
        core = patch.object(endpoints.client, "CoreV1Api")
        self.core = core.start().return_value
        self.addCleanup(core.stop)
        self.core.read_namespaced_service.return_value = self.service
        apps = patch.object(endpoints.client, "AppsV1Api")
        self.apps = apps.start().return_value
        self.addCleanup(apps.stop)
        self.apps.read_namespaced_deployment.return_value = self.deployment

    def test_managed_service_is_verified_without_external_allowlist(self):
        # Arrange
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": ""}):
            # Act
            urls = endpoints.managed_service_urls("control", "iris-service", "iris")

        # Assert
        self.assertEqual(urls, {"http": "http://iris-service.control.svc.cluster.local:18000",
                               "metrics": "http://iris-service.control.svc.cluster.local:18002/metrics"})
        self.core.read_namespaced_service.assert_called_once_with("iris-service", "control")
        self.apps.read_namespaced_deployment.assert_called_once_with("iris", "control")

    def test_foreign_selectors_and_external_services_are_rejected(self):
        # Arrange
        cases: list[dict[str, Any]] = [
            {"selector": {"app": "mlflow"}}, {"type": "ExternalName", "external_name": "evil.example"},
            {"external_i_ps": ["169.254.169.254"]},
        ]

        # Act / Assert
        for changes in cases:
            with self.subTest(changes=changes):
                original = self.service.spec
                self.service.spec = client.V1ServiceSpec(**{
                    **original.to_dict(), **changes,
                })
                try:
                    with self.assertRaises(ValueError):
                        endpoints.managed_service_urls("control", "iris-service", "iris")
                finally:
                    self.service.spec = original

    def test_forged_namespace_or_deployment_selector_is_rejected(self):
        # Arrange / Act / Assert
        with self.assertRaises(ValueError):
            endpoints.managed_service_urls("control.evil", "iris-service", "iris")
        self.core.read_namespaced_service.assert_not_called()
        self.deployment.spec.selector.match_labels = {"app": "other"}
        with self.assertRaises(ValueError):
            endpoints.managed_service_urls("control", "iris-service", "iris")


class OutboundRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await TritonService.close_all_clients()

    async def test_both_metrics_paths_block_ssrf_before_http(self):
        # Arrange
        service = TritonService("http://approved.example:8000")
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "approved.example:8000"}), patch.object(
            service, "_get_client"
        ) as http:
            # Act
            snapshot = await service.collect_metrics_snapshot("http://169.254.169.254/latest/meta-data/")
            inference = await service.collect_inference_metrics_snapshot("http://127.0.0.1:8000/private")

        # Assert
        self.assertIn("not approved", snapshot["error"])
        self.assertIn("not approved", inference["error"])
        http.assert_not_called()

    async def test_inference_and_generate_cannot_bypass_destination_policy(self):
        # Arrange
        service = TritonService("http://169.254.169.254")
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": ""}), patch.object(service, "_get_client") as http:
            # Act / Assert
            with self.assertRaises(ValueError):
                await service.infer_model_raw("iris", "1", b"{}", "application/json")
            with self.assertRaises(ValueError):
                await service.generate_model_raw("iris", b"{}", "application/json")
        http.assert_not_called()

    async def test_redirect_to_private_host_is_not_followed(self):
        # Arrange
        seen = []

        def respond(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})

        original_client = httpx.AsyncClient
        service = TritonService("http://approved.example:8000")
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "approved.example:8002"}), patch(
            "app.services.triton.client.httpx.AsyncClient",
            side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
        ):
            # Act
            result = await service.collect_metrics_snapshot("http://approved.example:8002/metrics")

        # Assert
        self.assertEqual(seen, ["http://approved.example:8002/metrics"])
        self.assertIsNotNone(result["error"])

    async def test_managed_requests_ignore_user_controlled_ingress_and_metrics_urls(self):
        # Arrange
        row = TritonInstanceEntity(url="http://evil.example", name="iris", is_self_deployed=True,
                                   deployment_namespace="control", deployment_name="iris",
                                   deployment_service_name="iris-service")
        service = TritonService(row.url, managed_instance=row)
        urls = {"http": "http://iris-service.control.svc.cluster.local:18000",
                "metrics": "http://iris-service.control.svc.cluster.local:18002/metrics"}
        http = AsyncMock()
        http.get.return_value = httpx.Response(200, content=b"", request=httpx.Request("GET", urls["metrics"]))
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": ""}), patch.object(
            endpoints, "managed_service_urls", return_value=urls,
        ) as verify, patch.object(service, "_get_client", return_value=http):
            # Act
            await service.is_ready()
            await service.collect_metrics_snapshot("http://169.254.169.254/latest/meta-data/")

        # Assert
        self.assertEqual([call.args[0] for call in http.get.call_args_list],
                         [urls["http"] + "/v2/health/ready", urls["metrics"]])
        verify.assert_called_once_with("control", "iris-service", "iris")
        self.assertFalse(service.trust_env)

    async def test_registration_checks_both_endpoints_before_connecting(self):
        # Arrange
        request = CreateTritonInstanceRequest(url="http://approved.example:8000",
                                             metrics_url="http://169.254.169.254/metrics")
        with patch.dict(os.environ, {"TRITON_ALLOWED_ENDPOINTS": "approved.example:8000"}), patch.object(
            instances, "TritonService"
        ) as service:
            # Act / Assert
            with self.assertRaises(BadRequestError):
                await instances.create_instance(request, None, {"role": "member"})
        service.assert_not_called()
