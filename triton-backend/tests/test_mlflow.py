"""Unit tests for singleton MLflow installation and proxy behavior."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.api import mlflow_api
from app.db.entities import MlflowEntity
from app.exceptions import BadGatewayError, BadRequestError, ConflictError
from app.schemas import InstallMlflowRequest
from app.services.mlflow import installer, proxy
from app.services.mlflow import kubernetes as k8s


class MlflowTests(unittest.TestCase):
    def _request(self) -> InstallMlflowRequest:
        return InstallMlflowRequest(
            installation_name="mlflow",
            image="ghcr.io/mlflow/mlflow:v2.15.1",
        )

    def test_InstallMlflow_NameProvided_AppliesNamedResources(self) -> None:
        with (
            patch("app.services.mlflow.installer.mlflow.get", return_value=None),
            patch(
                "app.services.mlflow.installer.k8s.apply_installation_resources",
                return_value=["Deployment/mlflow", "Service/mlflow-service"],
            ) as apply_resources,
            patch(
                "app.services.mlflow.installer.mlflow.save",
                side_effect=lambda _session, entity: entity,
            ),
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

    def test_Status_ReadyRecordWithoutRunningPod_ReturnsCreating(self) -> None:
        entity = MlflowEntity(
            namespace="triton-control",
            deployment_name="mlflow",
            service_name="mlflow-service",
            image="mlflow:test",
            status="ready",
            status_message="previous",
        )
        with (
            patch("app.services.mlflow.installer.mlflow.get", return_value=entity),
            patch(
                "app.services.mlflow.installer.k8s.read_installation_readiness",
                return_value=(False, "still starting"),
            ),
        ):
            result = installer.get_mlflow_status(SimpleNamespace())

        self.assertEqual(result.status, "creating")
        self.assertEqual(result.status_message, "still starting")
        self.assertFalse(result.ready)

    def test_InstallMlflow_ExistingInstallation_RaisesConflict(self) -> None:
        with patch("app.services.mlflow.installer.mlflow.get", return_value=object()):
            with self.assertRaises(ConflictError):
                installer.install_mlflow(self._request(), SimpleNamespace())

    def test_InstallMlflow_ApplyFails_CleansUpDatabaseRecord(self) -> None:
        saved: list[MlflowEntity] = []

        def save_entity(_session: object, entity: MlflowEntity) -> MlflowEntity:
            saved.append(entity)
            return entity

        with (
            patch("app.services.mlflow.installer.mlflow.get", return_value=None),
            patch(
                "app.services.mlflow.installer.mlflow.save",
                side_effect=save_entity,
            ),
            patch(
                "app.services.mlflow.installer.k8s.apply_installation_resources",
                side_effect=BadGatewayError("no cluster"),
            ),
            patch("app.services.mlflow.installer.mlflow.delete") as delete,
        ):
            with self.assertRaises(BadGatewayError):
                installer.install_mlflow(self._request(), SimpleNamespace())

        self.assertEqual(saved[-1].status, "failed")
        delete.assert_called_once()

    def test_UninstallMlflow_ControlNamespace_DeletesResources(self) -> None:
        entity = SimpleNamespace(
            namespace="triton-control",
            deployment_name="mlflow",
            service_name="mlflow-service",
        )
        with (
            patch("app.services.mlflow.installer.mlflow.get", return_value=entity),
            patch(
                "app.services.mlflow.installer.mlflow.save",
            ),
            patch(
                "app.services.mlflow.installer.k8s.delete_installation_resources",
                return_value="Deployment/mlflow",
            ) as delete_resources,
            patch("app.services.mlflow.installer.mlflow.delete"),
        ):
            result = installer.uninstall_mlflow(SimpleNamespace())

        delete_resources.assert_called_once_with(
            namespace="triton-control",
            deployment_name="mlflow",
            service_name="mlflow-service",
        )
        self.assertEqual(result.status, "deleted")

    def test_UninstallMlflow_OtherNamespace_DeletesNamespace(self) -> None:
        entity = SimpleNamespace(
            namespace="legacy-mlflow",
            deployment_name="mlflow",
            service_name="mlflow-service",
        )
        with (
            patch("app.services.mlflow.installer.mlflow.get", return_value=entity),
            patch(
                "app.services.mlflow.installer.mlflow.save",
            ),
            patch(
                "app.services.mlflow.installer.is_running_in_cluster",
                return_value=False,
            ),
            patch(
                "app.services.mlflow.installer.k8s.delete_namespace",
                return_value="deleted",
            ) as delete_namespace,
            patch("app.services.mlflow.installer.mlflow.delete"),
        ):
            installer.uninstall_mlflow(SimpleNamespace())

        delete_namespace.assert_called_once_with("legacy-mlflow")

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

    def test_GetProxyServerUrl_FailedInstallation_RaisesBadRequest(self) -> None:
        with patch(
            "app.services.mlflow.installer.mlflow.get",
            return_value=SimpleNamespace(status="failed"),
        ):
            with self.assertRaises(BadRequestError):
                installer.get_proxy_server_url(SimpleNamespace())

    def test_KubernetesReadiness_CoversReadyErrorAndPending(self) -> None:
        with (
            patch("app.services.mlflow.kubernetes._client", return_value=object()),
            patch(
                "app.services.mlflow.kubernetes._running_pod_name",
                return_value="mlflow-123",
            ),
        ):
            self.assertTrue(k8s.read_installation_readiness("ns", "mlflow")[0])

        with (
            patch("app.services.mlflow.kubernetes._client", return_value=object()),
            patch(
                "app.services.mlflow.kubernetes._running_pod_name",
                return_value="",
            ),
            patch("app.services.mlflow.kubernetes._pod_error_reason", return_value="ImagePullBackOff"),
        ):
            ready, message = k8s.read_installation_readiness("ns", "mlflow")
            self.assertFalse(ready)
            self.assertIn("ImagePullBackOff", message)

        with (
            patch("app.services.mlflow.kubernetes._client", return_value=object()),
            patch(
                "app.services.mlflow.kubernetes._running_pod_name",
                return_value="",
            ),
            patch("app.services.mlflow.kubernetes._pod_error_reason", return_value=""),
        ):
            self.assertIn("not Running yet", k8s.read_installation_readiness("ns", "mlflow")[1])

    def test_KubernetesPodHelpers_DetectRunningAndTerminalReasons(self) -> None:
        running = SimpleNamespace(
            metadata=SimpleNamespace(name="mlflow-123"),
            status=SimpleNamespace(phase="Running", container_statuses=[]),
        )
        failed = SimpleNamespace(
            metadata=SimpleNamespace(name="mlflow-456"),
            status=SimpleNamespace(phase="Failed", container_statuses=[]),
        )
        waiting = SimpleNamespace(
            metadata=SimpleNamespace(name="mlflow-789"),
            status=SimpleNamespace(
                phase="Pending",
                container_statuses=[
                    SimpleNamespace(state=SimpleNamespace(waiting=SimpleNamespace(reason="ImagePullBackOff")))
                ],
            ),
        )
        with patch("kubernetes.client.CoreV1Api") as core:
            core.return_value.list_namespaced_pod.return_value = SimpleNamespace(items=[running])
            self.assertEqual(k8s._running_pod_name(object(), "ns", "mlflow"), "mlflow-123")
            core.return_value.list_namespaced_pod.return_value = SimpleNamespace(items=[failed])
            self.assertEqual(k8s._pod_error_reason(object(), "ns", "mlflow"), "Failed")
            core.return_value.list_namespaced_pod.return_value = SimpleNamespace(items=[waiting])
            self.assertEqual(k8s._pod_error_reason(object(), "ns", "mlflow"), "ImagePullBackOff")

    def test_DeleteInstallationResources_IgnoresNotFound(self) -> None:
        apps = MagicMock()
        core = MagicMock()
        apps.delete_namespaced_deployment.side_effect = ApiException(status=404)
        core.delete_namespaced_service.side_effect = ApiException(status=404)
        core.delete_namespaced_persistent_volume_claim.side_effect = ApiException(status=404)
        core.delete_namespaced_secret.side_effect = ApiException(status=404)
        with (
            patch("kubernetes.client.AppsV1Api", return_value=apps),
            patch(
                "kubernetes.client.CoreV1Api",
                return_value=core,
            ),
            patch("app.services.mlflow.kubernetes._client", return_value=object()),
        ):
            result = k8s.delete_installation_resources("ns", "mlflow", "mlflow-service")

        self.assertEqual(result, "No MLflow resources found to delete.")

    def test_DirectProxy_ReturnsFilteredResponseAndRewritesLocation(self) -> None:
        upstream = SimpleNamespace(
            headers={"location": "/#/runs", "content-length": "12", "x-test": "yes"},
            content=b"ok",
            status_code=302,
        )
        client = MagicMock()
        client.__enter__.return_value.request.return_value = upstream
        with patch("app.services.mlflow.proxy.httpx.Client", return_value=client):
            response = proxy._direct_proxy_http_sync(
                "http://mlflow:5000",
                "api/test",
                "GET",
                {},
                [("x", "1")],
                b"",
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/api/mlflow/proxy/#/runs")
        self.assertEqual(response.headers["x-test"], "yes")

    def test_DirectProxy_HttpError_RaisesBadGateway(self) -> None:
        client = MagicMock()
        client.__enter__.return_value.request.side_effect = httpx.ConnectError("offline")
        with patch("app.services.mlflow.proxy.httpx.Client", return_value=client):
            with self.assertRaises(BadGatewayError):
                proxy._direct_proxy_http_sync("http://mlflow:5000", "", "GET", {}, [], b"")

    def test_ServiceIdentity_InvalidUrl_RaisesBadGateway(self) -> None:
        with self.assertRaises(BadGatewayError):
            proxy._service_identity("http://mlflow:5000")

    def test_ProxyRewriteLocation_AbsolutePath_AddsBasePrefix(self) -> None:
        headers = {"location": "/#/experiments"}
        proxy._rewrite_location_header(headers, "http://mlflow-service.triton-control.svc.cluster.local:5000")
        self.assertEqual(headers["location"], "/api/mlflow/proxy/#/experiments")

    def test_ProxyRequestSkipHeaders_StripsConditionalCacheHeaders(self) -> None:
        self.assertIn("if-none-match", proxy._REQUEST_SKIP_HEADERS)
        self.assertIn("if-modified-since", proxy._REQUEST_SKIP_HEADERS)
