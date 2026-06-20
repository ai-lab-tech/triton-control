"""Unit tests for singleton MLflow installation and proxy behavior."""

import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException

from app.api import mlflow_api
from app.exceptions import BadRequestError
from app.schemas import InstallMlflowRequest
from app.services.mlflow import installer
from app.services.mlflow import kubernetes as k8s
from app.services.mlflow import proxy


class MlflowTests(unittest.TestCase):
    def _request(self) -> InstallMlflowRequest:
        return InstallMlflowRequest(
            installation_name="mlflow",
            image="ghcr.io/mlflow/mlflow:v2.15.1",
        )

    def test_InstallMlflow_NameProvided_AppliesNamedResources(self) -> None:
        with patch("app.services.mlflow.installer.mlflow.get", return_value=None), patch(
            "app.services.mlflow.installer.k8s.apply_installation_resources",
            return_value=["Deployment/mlflow", "Service/mlflow-service"],
        ) as apply_resources, patch(
            "app.services.mlflow.installer.mlflow.save",
            side_effect=lambda _session, entity: entity,
        ):
            response = installer.install_mlflow(self._request(), SimpleNamespace())

        apply_resources.assert_called_once()
        self.assertEqual(response.namespace, "triton-control")
        self.assertEqual(response.deployment_name, "mlflow")
        self.assertEqual(response.service_name, "mlflow-service")
        self.assertIn("Deployment/mlflow", response.applied_resources)

    def test_Status_NotInstalled_ReturnsDefaultBasePath(self) -> None:
        with patch("app.services.mlflow.installer.mlflow.get", return_value=None):
            result = installer.get_mlflow_status(SimpleNamespace())

        self.assertFalse(result.installed)
        self.assertEqual(result.status, "not_installed")
        self.assertEqual(result.base_path, "/api/mlflow/proxy/")

    def test_Manifests_DockerConfigProvided_AddsImagePullSecret(self) -> None:
        dockerconfigjson = '{"auths":{"registry.example":{"auth":"token"}}}'
        request = self._request().model_copy(update={"dockerconfigjson": dockerconfigjson})

        manifests = k8s._manifests(request, "mlflow", "mlflow", "mlflow-service")

        secret = manifests[0]
        pvc = manifests[1]
        deployment = manifests[2]
        service = manifests[3]
        self.assertEqual(secret["kind"], "Secret")
        self.assertEqual(secret["stringData"][".dockerconfigjson"], dockerconfigjson)
        self.assertEqual(pvc["kind"], "PersistentVolumeClaim")
        self.assertEqual(deployment["spec"]["template"]["spec"]["imagePullSecrets"], [{"name": "mlflow-pull-secret"}])
        self.assertEqual(service["metadata"]["name"], "mlflow-service")

    def test_Manifests_MlflowPod_UsesRestrictedSecurityContext(self) -> None:
        manifests = k8s._manifests(self._request(), "mlflow", "mlflow", "mlflow-service")

        deployment = next(manifest for manifest in manifests if manifest["kind"] == "Deployment")
        pod_spec = deployment["spec"]["template"]["spec"]
        container = pod_spec["containers"][0]

        self.assertFalse(pod_spec["automountServiceAccountToken"])
        self.assertEqual(
            pod_spec["securityContext"],
            {
                "runAsNonRoot": True,
                "runAsUser": 10001,
                "runAsGroup": 10001,
                "fsGroup": 10001,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
        )
        self.assertEqual(
            container["securityContext"],
            {
                "allowPrivilegeEscalation": False,
                "capabilities": {"drop": ["ALL"]},
            },
        )

    def test_ApiStatus_ViewerIsRejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            mlflow_api.get_mlflow_status(session=SimpleNamespace(), claims={"role": "viewer"})
        self.assertEqual(raised.exception.status_code, 403)

    def test_ApiStatus_MemberDelegates(self) -> None:
        expected = mlflow_api.MlflowStatusResponse(
            installed=True,
            status="ready",
            ready=True,
            status_message="ok",
            base_path="/api/mlflow/proxy/",
            installation=mlflow_api.MlflowInstallResponse(
                namespace="triton-control",
                deployment_name="mlflow",
                service_name="mlflow-service",
                image="ghcr.io/mlflow/mlflow:v2.15.1",
                applied_resources=["Deployment/mlflow"],
            ),
        )
        with patch("app.api.mlflow_api.installer.get_mlflow_status", return_value=expected):
            result = mlflow_api.get_mlflow_status(session=SimpleNamespace(), claims={"role": "member"})
        self.assertEqual(result, expected)

    def test_ApiInstall_MemberDelegates(self) -> None:
        payload = self._request()
        expected = mlflow_api.MlflowInstallResponse(
            namespace="triton-control",
            deployment_name="mlflow",
            service_name="mlflow-service",
            image="ghcr.io/mlflow/mlflow:v2.15.1",
            applied_resources=["Deployment/mlflow"],
        )
        with patch("app.api.mlflow_api.installer.install_mlflow", return_value=expected) as mocked:
            result = mlflow_api.install_mlflow(payload, session=SimpleNamespace(), claims={"role": "member"})

        mocked.assert_called_once()
        self.assertEqual(result, expected)

    def test_ApiDelete_MemberDelegates(self) -> None:
        expected = mlflow_api.MlflowDeleteResponse(
            status="deleted",
            message="Namespace deletion requested.",
            namespace="triton-control",
        )
        with patch("app.api.mlflow_api.installer.uninstall_mlflow", return_value=expected) as mocked:
            result = mlflow_api.uninstall_mlflow(session=SimpleNamespace(), claims={"role": "member"})

        mocked.assert_called_once_with(ANY)
        self.assertEqual(result, expected)

    def test_ApiProxy_MemberDelegates(self) -> None:
        response = SimpleNamespace(status_code=200)
        with patch("app.api.mlflow_api.proxy.proxy_http", AsyncMock(return_value=response)) as mocked:
            result = asyncio.run(
                mlflow_api.proxy_mlflow(
                    request=SimpleNamespace(),
                    path="api/2.0/mlflow/experiments/list",
                    session=SimpleNamespace(),
                    claims={"role": "member"},
                )
            )
        mocked.assert_awaited_once()
        self.assertIs(result, response)

    def test_GetProxyServerUrl_NotInstalled_RaisesBadRequest(self) -> None:
        with patch("app.services.mlflow.installer.mlflow.get", return_value=None):
            with self.assertRaises(BadRequestError):
                installer.get_proxy_server_url(SimpleNamespace())

    def test_ProxyRewriteLocation_AbsolutePath_AddsBasePrefix(self) -> None:
        headers = {"location": "/#/experiments"}
        proxy._rewrite_location_header(headers, "http://mlflow-service.triton-control.svc.cluster.local:5000")
        self.assertEqual(headers["location"], "/api/mlflow/proxy/#/experiments")

    def test_ProxyRequestSkipHeaders_StripsConditionalCacheHeaders(self) -> None:
        self.assertIn("if-none-match", proxy._REQUEST_SKIP_HEADERS)
        self.assertIn("if-modified-since", proxy._REQUEST_SKIP_HEADERS)
