"""Unit tests for per-user code-server workspace behavior."""

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, patch

import httpx
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.api import code_server_api
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.schemas import CreateCodeServerRequest
from app.services.code_server import kubernetes as k8s
from app.services.code_server import proxy as code_proxy
from app.services.code_server import workspaces


class CodeServerTests(unittest.TestCase):
    def _request(self) -> CreateCodeServerRequest:
        return CreateCodeServerRequest(
            name="Dev Workspace",
            image="nvcr.io/nvidia/tritonserver:25.02-py3",
            storage_size="30Gi",
            cpu="2",
            memory="4Gi",
        )

    def _row(self, **overrides: object) -> SimpleNamespace:
        values = {
            "id": 2,
            "owner_user_id": 7,
            "name": "dev-workspace",
            "namespace": "triton-control",
            "statefulset_name": "code-7-dev-workspace",
            "service_name": "code-7-dev-workspace-svc",
            "secret_name": "code-7-dev-workspace-secret",
            "image": "nvcr.io/nvidia/tritonserver:25.02-py3",
            "url": "http://code-7-dev-workspace-svc.triton-control.svc.cluster.local:8080",
            "password_enc": "",
            "status": "creating",
            "status_message": "Pending",
            "applied_resources": ["StatefulSet/code-7-dev-workspace"],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_CreateCodeServerRequest_NormalizesName(self) -> None:
        request = self._request()

        self.assertEqual(request.name, "dev-workspace")
        self.assertFalse(request.image_has_code_server)

    def test_CreateCodeServerRequest_InvalidValues_RaiseValidationError(self) -> None:
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(name="!!!", image="triton")
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(
                name="workspace",
                image=" ",
            )
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(name="workspace", image="triton", theme="Unknown Theme")
    def test_Manifests_CreateStatefulSetWithPersistentWorkspaceAndProxyAuth(self) -> None:
        request = self._request()

        manifests = k8s._manifests(
            request,
            "triton-control",
            "code-7-dev-workspace",
            "code-7-dev-workspace-svc",
            "code-7-dev-workspace-secret",
        )

        secret = manifests[0]
        extension_configmap = manifests[1]
        statefulset = manifests[2]
        service = manifests[3]
        pod_spec = statefulset["spec"]["template"]["spec"]
        container = statefulset["spec"]["template"]["spec"]["containers"][0]

        self.assertEqual(secret["stringData"]["AUTH_MODE"], "triton-control-proxy")
        self.assertEqual(extension_configmap["kind"], "ConfigMap")
        self.assertIn("triton-control-deploy.vsix.b64", extension_configmap["data"])
        self.assertEqual(statefulset["kind"], "StatefulSet")
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
                "readOnlyRootFilesystem": False,
                "capabilities": {"drop": ["ALL"]},
            },
        )
        self.assertIn({"name": "HOME", "value": "/workspace"}, container["env"])
        self.assertIn({"name": "VSCODE_RECONNECTION_GRACE_TIME", "value": "30000"}, container["env"])
        self.assertIn({"name": "NODE_TLS_REJECT_UNAUTHORIZED", "value": "0"}, container["env"])
        self.assertEqual(container["image"], "nvcr.io/nvidia/tritonserver:25.02-py3")
        self.assertIn("--method=standalone --prefix=/workspace/.local", container["args"][0])
        self.assertIn("exec \"$CODE_SERVER_BIN\" --bind-addr 0.0.0.0:8080", container["args"][0])
        self.assertIn("--reconnection-grace-time 30", container["args"][0])
        self.assertIn("--auth none", container["args"][0])
        self.assertIn("\"workbench.colorTheme\":\"Default Dark+\"", container["args"][0])
        self.assertIn("--install-extension ms-python.python", container["args"][0])
        self.assertIn("triton-control-deploy.vsix.b64", container["args"][0])
        self.assertIn("--install-extension \"$TRITON_DEPLOY_EXTENSION_VSIX\"", container["args"][0])
        self.assertIn("rm -f /workspace/.code-server/extensions/.obsolete", container["args"][0])
        self.assertIn("--user-data-dir /workspace/.code-server/user-data", container["args"][0])
        self.assertIn("--extensions-dir /workspace/.code-server/extensions", container["args"][0])
        self.assertIn(
            {
                "name": "triton-deploy-extension",
                "mountPath": "/opt/triton-control/extensions/triton-deploy",
                "readOnly": True,
            },
            container["volumeMounts"],
        )
        self.assertEqual(container["startupProbe"]["httpGet"], {"path": "/", "port": "http"})
        self.assertEqual(container["readinessProbe"]["httpGet"], {"path": "/", "port": "http"})
        self.assertEqual(
            statefulset["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"],
            "30Gi",
        )
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)
        self.assertEqual([manifest["kind"] for manifest in manifests], ["Secret", "ConfigMap", "StatefulSet", "Service"])

    def test_Manifests_UsesSelectedCodeServerTheme(self) -> None:
        request = self._request().model_copy(update={"theme": "Monokai"})

        manifests = k8s._manifests(
            request,
            "triton-control",
            "code-7-dev-workspace",
            "code-7-dev-workspace-svc",
            "code-7-dev-workspace-secret",
        )

        container = manifests[2]["spec"]["template"]["spec"]["containers"][0]

        self.assertIn("\"workbench.colorTheme\":\"Monokai\"", container["args"][0])

    def test_Manifests_ImageAlreadyHasCodeServer_SkipsInstallScript(self) -> None:
        request = self._request().model_copy(update={"image_has_code_server": True})

        manifests = k8s._manifests(
            request,
            "triton-control",
            "code-7-dev-workspace",
            "code-7-dev-workspace-svc",
            "code-7-dev-workspace-secret",
        )

        statefulset = next(manifest for manifest in manifests if manifest.get("kind") == "StatefulSet")
        container = statefulset["spec"]["template"]["spec"]["containers"][0]

        self.assertIn("command -v code-server", container["args"][0])
        self.assertNotIn("install.sh", container["args"][0])
        
    def test_TritonDeployExtensionDir_ConfiguredParentDirectory_UsesChildExtension(self) -> None:
        configured = Path(__file__).resolve().parents[2] / "code-server-extensions"

        with patch.dict(
            "os.environ",
            {"TRITON_DEPLOY_CODE_SERVER_EXTENSION_DIR": str(configured)},
        ):
            self.assertEqual(
                k8s._triton_deploy_extension_dir(),
                configured / "triton-deploy",
            )

    def test_Manifests_DockerConfigProvided_AddsImagePullSecret(self) -> None:
        request = self._request().model_copy(
            update={"dockerconfigjson": '{"auths":{"registry.example":{"auth":"token"}}}'},
        )

        manifests = k8s._manifests(
            request,
            "triton-control",
            "code-7-dev-workspace",
            "code-7-dev-workspace-svc",
            "code-7-dev-workspace-secret",
        )

        self.assertEqual(manifests[0]["kind"], "Secret")
        self.assertEqual(manifests[0]["type"], "kubernetes.io/dockerconfigjson")
        statefulset = manifests[3]
        self.assertEqual(
            statefulset["spec"]["template"]["spec"]["imagePullSecrets"],
            [{"name": "code-7-dev-workspace-pull-secret"}],
        )

    def test_CreateCodeServer_KubernetesDisabled_RaisesBadRequest(self) -> None:
        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=False):
            with self.assertRaises(BadRequestError):
                workspaces.create_code_server(self._request(), SimpleNamespace(), {"email": "u@example.com"})

    def test_CreateCodeServer_NewWorkspace_CreatesOwnedRecord(self) -> None:
        created: dict[str, object] = {}
        user = SimpleNamespace(id=7)

        def create_row(_session: object, **kwargs: object) -> SimpleNamespace:
            created.update(kwargs)
            return self._row(id=9, **kwargs)

        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=user,
        ), patch("app.services.code_server.workspaces._workspace_namespace", return_value="triton-control"), patch(
            "app.services.code_server.workspaces.k8s.apply_code_server_resources",
            return_value=["Secret/code-7-dev-workspace-secret", "StatefulSet/code-7-dev-workspace"],
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_first_for_owner",
            return_value=None,
        ), patch(
            "app.services.code_server.workspaces.code_servers.create",
            side_effect=create_row,
        ) as create, patch(
            "app.services.code_server.workspaces.code_servers.save",
            side_effect=lambda _session, row: row,
        ) as save:
            result = workspaces.create_code_server(self._request(), SimpleNamespace(), {"email": "u@example.com"})

        create.assert_called_once()
        save.assert_called_once()
        self.assertEqual(result.id, 9)
        self.assertEqual(result.url, "/api/code-servers/9/proxy/?folder=/workspace")
        self.assertEqual(created["owner_user_id"], 7)
        self.assertEqual(created["name"], "dev-workspace")
        self.assertEqual(created["statefulset_name"], "code-7-dev-workspace")

    def test_CreateCodeServer_ExistingWorkspace_RaisesBadRequest(self) -> None:
        existing = self._row(status="ready", image="old-image")
        user = SimpleNamespace(id=7)

        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=user,
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_first_for_owner",
            return_value=existing,
        ), patch("app.services.code_server.workspaces.k8s.apply_code_server_resources") as apply_resources:
            with self.assertRaises(BadRequestError):
                workspaces.create_code_server(self._request(), SimpleNamespace(), {"email": "u@example.com"})

        apply_resources.assert_not_called()

    def test_GetCodeServer_NotFound_RaisesNotFound(self) -> None:
        with patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=SimpleNamespace(id=7),
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_by_id",
            return_value=None,
        ):
            with self.assertRaises(NotFoundError):
                workspaces.get_code_server(SimpleNamespace(), {"email": "u@example.com"}, 99)

    def test_GetCodeServer_StatusChanges_PersistsReadyStatus(self) -> None:
        row = self._row(status="creating", status_message="Pending")

        with patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=SimpleNamespace(id=7),
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_by_id",
            return_value=row,
        ), patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.k8s.read_status",
            return_value=("ready", "pod Ready"),
        ), patch(
            "app.services.code_server.workspaces.code_servers.save",
            side_effect=lambda _session, saved: saved,
        ) as save:
            result = workspaces.get_code_server(SimpleNamespace(), {"email": "u@example.com"}, 2)

        save.assert_called_once()
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.status_message, "pod Ready")

    def test_GetCodeServer_KubernetesDisabled_MarksStatusUnavailable(self) -> None:
        row = self._row(status="ready", status_message="old ready")

        with patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=SimpleNamespace(id=7),
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_by_id",
            return_value=row,
        ), patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=False), patch(
            "app.services.code_server.workspaces.code_servers.save",
            side_effect=lambda _session, saved: saved,
        ) as save:
            result = workspaces.get_code_server(SimpleNamespace(), {"email": "u@example.com"}, 2)

        save.assert_called_once()
        self.assertEqual(result.status, "unavailable")
        self.assertIn("Kubernetes is disabled", result.status_message)

    def test_DeleteCodeServer_Success_DeletesKubernetesResourcesAndRecord(self) -> None:
        row = self._row()

        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=SimpleNamespace(id=7),
        ), patch("app.services.code_server.workspaces.code_servers.find_by_id", return_value=row), patch(
            "app.services.code_server.workspaces.k8s.delete_code_server_resources",
            return_value="StatefulSet/code-7-dev-workspace",
        ) as delete_k8s, patch("app.services.code_server.workspaces.code_servers.delete") as delete_row:
            result = workspaces.delete_code_server(SimpleNamespace(), {"email": "u@example.com"}, 2)

        delete_k8s.assert_called_once_with(
            namespace="triton-control",
            statefulset_name="code-7-dev-workspace",
            service_name="code-7-dev-workspace-svc",
            secret_name="code-7-dev-workspace-secret",
        )
        delete_row.assert_called_once()
        self.assertEqual(result.status, "deleted")

    def test_DeleteCodeServer_KubernetesError_RaisesBadRequest(self) -> None:
        row = self._row()

        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=SimpleNamespace(id=7),
        ), patch("app.services.code_server.workspaces.code_servers.find_by_id", return_value=row), patch(
            "app.services.code_server.workspaces.k8s.delete_code_server_resources",
            side_effect=RuntimeError("no kubeconfig"),
        ):
            with self.assertRaises(BadRequestError):
                workspaces.delete_code_server(SimpleNamespace(), {"email": "u@example.com"}, 2)

    def test_WorkspaceNamespace_OutOfClusterWithoutEnv_UsesControlNamespace(self) -> None:
        with patch("app.services.code_server.workspaces.is_running_in_cluster", return_value=False), patch.dict(
            "os.environ",
            {},
            clear=True,
        ):
            self.assertEqual(workspaces._workspace_namespace(), "triton-control")

    def test_WorkspaceNamespace_InCluster_UsesControlNamespace(self) -> None:
        with patch("app.services.code_server.workspaces.is_running_in_cluster", return_value=True), patch(
            "app.services.code_server.workspaces.in_cluster_namespace",
            return_value="triton-control",
        ):
            self.assertEqual(workspaces._workspace_namespace(), "triton-control")

    def test_WorkspaceNamespace_ConfiguredEnv_UsesConfiguredNamespace(self) -> None:
        with patch("app.services.code_server.workspaces.is_running_in_cluster", return_value=False), patch.dict(
            "os.environ",
            {"KUBERNETES_NAMESPACE": "dev-tools"},
            clear=True,
        ):
            self.assertEqual(workspaces._workspace_namespace(), "dev-tools")

    def test_ListCodeServers_ReturnsOnlyCurrentOwnerRows(self) -> None:
        user = SimpleNamespace(id=7)
        row = SimpleNamespace(
            id=2,
            owner_user_id=7,
            name="workspace",
            namespace="triton-control",
            statefulset_name="code-7-workspace",
            service_name="code-7-workspace-svc",
            image="nvcr.io/nvidia/tritonserver:25.02-py3",
            url="http://code-7-workspace-svc.triton-control.svc.cluster.local:8080",
            password_enc="",
            status="ready",
            status_message="Ready",
            applied_resources=["StatefulSet/code-7-workspace"],
        )

        with patch("app.services.code_server.workspaces.require_user_entity", return_value=user), patch(
            "app.services.code_server.workspaces.code_servers.list_for_owner",
            return_value=[row],
        ) as list_for_owner, patch("app.services.code_server.workspaces._refresh_status") as refresh_status:
            result = workspaces.list_code_servers(SimpleNamespace(), {"email": "u@example.com"})

        list_for_owner.assert_called_once_with(ANY, 7)
        refresh_status.assert_called_once_with(ANY, row)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, "/api/code-servers/2/proxy/?folder=/workspace")

    def test_GetCodeServer_DifferentOwner_RaisesForbidden(self) -> None:
        user = SimpleNamespace(id=7)
        row = SimpleNamespace(owner_user_id=8)

        with patch("app.services.code_server.workspaces.require_user_entity", return_value=user), patch(
            "app.services.code_server.workspaces.code_servers.find_by_id",
            return_value=row,
        ):
            with self.assertRaises(ForbiddenError):
                workspaces.get_code_server(SimpleNamespace(), {"email": "u@example.com"}, 3)

    def test_ApiEndpoints_DelegateToWorkspaceService(self) -> None:
        dto = self._row()
        with patch("app.services.code_server.workspaces.list_code_servers", return_value=[dto]) as list_service:
            self.assertEqual(code_server_api.list_code_servers(session=SimpleNamespace(), claims={}), [dto])
        with patch("app.services.code_server.workspaces.create_code_server", return_value=dto) as create_service:
            self.assertEqual(
                code_server_api.create_code_server(self._request(), session=SimpleNamespace(), claims={}),
                dto,
            )
        with patch("app.services.code_server.workspaces.get_code_server", return_value=dto) as get_service:
            self.assertEqual(code_server_api.get_code_server(2, session=SimpleNamespace(), claims={}), dto)
        with patch(
            "app.services.code_server.workspaces.delete_code_server",
            return_value=SimpleNamespace(status="deleted"),
        ) as delete_service:
            self.assertEqual(
                code_server_api.delete_code_server(2, session=SimpleNamespace(), claims={}).status,
                "deleted",
            )

        list_service.assert_called_once()
        create_service.assert_called_once()
        get_service.assert_called_once()
        delete_service.assert_called_once()

    def test_DeploymentNavigation_StoresAndConsumesPerUserTarget(self) -> None:
        claims = {"user_id": 42, "email": "u@example.com"}

        code_server_api.notify_code_server_deployment_navigation(
            code_server_api.CodeServerDeploymentNavigationRequest(instance_id=77),
            claims=claims,
        )

        first = code_server_api.consume_code_server_deployment_navigation(claims=claims)
        second = code_server_api.consume_code_server_deployment_navigation(claims=claims)

        self.assertEqual(first.instance_id, 77)
        self.assertIsNone(second.instance_id)

    def test_ApplyCodeServerResources_AppliesKubernetesObjects(self) -> None:
        applied: list[dict[str, object]] = []

        with patch("app.services.code_server.kubernetes.api_client", return_value=object()), patch(
            "app.services.code_server.kubernetes._ensure_namespace",
        ) as ensure_namespace, patch(
            "kubernetes.utils.create_from_dict",
            side_effect=lambda _api_client, data, **_kwargs: applied.append(data),
        ):
            result = k8s.apply_code_server_resources(
                self._request(),
                namespace="triton-control",
                statefulset_name="code-7-dev-workspace",
                service_name="code-7-dev-workspace-svc",
                secret_name="code-7-dev-workspace-secret",
            )

        ensure_namespace.assert_called_once()
        self.assertEqual(result, [
            "Secret/code-7-dev-workspace-secret",
            "ConfigMap/code-7-dev-workspace-triton-deploy-ext",
            "StatefulSet/code-7-dev-workspace",
            "Service/code-7-dev-workspace-svc",
        ])
        self.assertEqual(applied[2]["kind"], "StatefulSet")

    def test_ReadStatus_PodsReadyAndMissing_ReturnsStatus(self) -> None:
        ready_pod = SimpleNamespace(
            metadata=SimpleNamespace(name="code-0"),
            status=SimpleNamespace(
                phase="Running",
                conditions=[SimpleNamespace(type="Ready", status="True")],
            ),
        )
        core_api = SimpleNamespace(
            list_namespaced_pod=lambda **_kwargs: SimpleNamespace(items=[ready_pod]),
        )
        apps_api = SimpleNamespace(read_namespaced_stateful_set=lambda **_kwargs: SimpleNamespace())

        with patch("app.services.code_server.kubernetes.api_client", return_value=object()), patch(
            "kubernetes.client.AppsV1Api",
            return_value=apps_api,
        ), patch(
            "kubernetes.client.CoreV1Api",
            return_value=core_api,
        ):
            status, message = k8s.read_status("triton-control", "code-7-dev-workspace")

        self.assertEqual(status, "ready")
        self.assertIn("Ready", message)

        empty_core_api = SimpleNamespace(
            list_namespaced_pod=lambda **_kwargs: SimpleNamespace(items=[]),
        )
        with patch("app.services.code_server.kubernetes.api_client", return_value=object()), patch(
            "kubernetes.client.AppsV1Api",
            return_value=apps_api,
        ), patch(
            "kubernetes.client.CoreV1Api",
            return_value=empty_core_api,
        ):
            status, message = k8s.read_status("triton-control", "code-7-dev-workspace")

        self.assertEqual(status, "creating")
        self.assertIn("Waiting", message)

    def test_ReadStatus_StatefulSetMissing_ReturnsMissing(self) -> None:
        def raise_not_found(**_kwargs: object) -> object:
            raise ApiException(status=404, reason="Not Found")

        apps_api = SimpleNamespace(read_namespaced_stateful_set=raise_not_found)

        with patch("app.services.code_server.kubernetes.api_client", return_value=object()), patch(
            "kubernetes.client.AppsV1Api",
            return_value=apps_api,
        ):
            status, message = k8s.read_status("triton-control", "code-7-dev-workspace")

        self.assertEqual(status, "missing")
        self.assertIn("not found", message)

    def test_WorkspaceUrl_RewritesServicePort(self) -> None:
        self.assertEqual(
            k8s.workspace_url("ns", "svc"),
            "http://svc.ns.svc.cluster.local:8080",
        )

    def test_ProxyHttpSync_UsesKubernetesServiceProxyAndStripsFrameHeaders(self) -> None:
        calls: dict[str, object] = {}

        class Upstream:
            data = b"<html>code</html>"

            def release_conn(self) -> None:
                calls["released"] = True

        class Api:
            def call_api(self, resource_path: str, method: str, **kwargs: object) -> object:
                calls["resource_path"] = resource_path
                calls["method"] = method
                calls.update(kwargs)
                return Upstream(), 200, {
                    "Content-Type": "text/html",
                    "Content-Encoding": "gzip",
                    "X-Frame-Options": "DENY",
                }

        with patch("app.services.code_server.proxy.is_running_in_cluster", return_value=False), patch(
            "app.services.code_server.proxy.api_client",
            return_value=Api(),
        ):
            response = code_proxy._proxy_http_sync(
                self._row(),
                "index.html",
                "GET",
                {"accept": "text/html"},
                [("v", "1")],
                b"",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"<html>code</html>")
        self.assertEqual(calls["method"], "GET")
        self.assertEqual(calls["path_params"], {
            "namespace": "triton-control",
            "name": "code-7-dev-workspace-svc:http",
            "path": "index.html",
        })
        self.assertNotIn("x-frame-options", {key.lower() for key in response.headers})
        self.assertNotIn("content-encoding", {key.lower() for key in response.headers})
        self.assertTrue(calls["released"])

    def test_ProxyHttpSync_InClusterUsesDirectServiceDns(self) -> None:
        calls: dict[str, object] = {}

        class Client:
            def __init__(self, **kwargs: object) -> None:
                calls["client_kwargs"] = kwargs

            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def request(self, method: str, url: str, **kwargs: object) -> SimpleNamespace:
                calls["method"] = method
                calls["url"] = url
                calls.update(kwargs)
                return SimpleNamespace(
                    content=b"<html>direct</html>",
                    status_code=200,
                    headers={
                        "Content-Type": "text/html",
                        "Content-Encoding": "gzip",
                        "X-Frame-Options": "DENY",
                    },
                )

        with patch("app.services.code_server.proxy.is_running_in_cluster", return_value=True), patch(
            "app.services.code_server.proxy.httpx.Client",
            return_value=Client(),
        ):
            response = code_proxy._proxy_http_sync(
                self._row(),
                "stable/socket",
                "GET",
                {"accept": "text/html"},
                [("v", "1")],
                b"",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"<html>direct</html>")
        self.assertEqual(calls["method"], "GET")
        self.assertEqual(
            calls["url"],
            "http://code-7-dev-workspace-svc.triton-control.svc.cluster.local:8080/stable/socket?v=1",
        )
        self.assertNotIn("x-frame-options", {key.lower() for key in response.headers})
        self.assertNotIn("content-encoding", {key.lower() for key in response.headers})

    def test_ProxyHttpSync_InClusterRetriesStartupConnectionRefused(self) -> None:
        calls: dict[str, int] = {"count": 0}

        class Client:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def request(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
                calls["count"] += 1
                if calls["count"] == 1:
                    raise httpx.ConnectError("[Errno 111] Connection refused")
                return SimpleNamespace(
                    content=b"<html>ready</html>",
                    status_code=200,
                    headers={"Content-Type": "text/html"},
                )

        with patch("app.services.code_server.proxy.is_running_in_cluster", return_value=True), patch(
            "app.services.code_server.proxy.httpx.Client",
            return_value=Client(),
        ), patch("app.services.code_server.proxy._DIRECT_PROXY_RETRY_DELAY_SECONDS", 0):
            response = code_proxy._proxy_http_sync(
                self._row(),
                "",
                "GET",
                {"accept": "text/html"},
                [],
                b"",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"<html>ready</html>")
        self.assertEqual(calls["count"], 2)

    def test_WebsocketUpstream_InClusterUsesDirectServiceDns(self) -> None:
        with patch("app.services.code_server.proxy.is_running_in_cluster", return_value=True):
            upstream_url, headers, ssl_context = code_proxy._websocket_upstream(
                self._row(),
                "stable/socket",
                [("reconnectionToken", "abc")],
            )

        self.assertEqual(
            upstream_url,
            "ws://code-7-dev-workspace-svc.triton-control.svc.cluster.local:8080/stable/socket?reconnectionToken=abc",
        )
        self.assertEqual(headers, {})
        self.assertIsNone(ssl_context)

    def test_ApiError_FormatsStatusReasonAndBody(self) -> None:
        message = k8s._api_error(SimpleNamespace(status=500, reason="boom", body="details"))
        self.assertEqual(message, "Kubernetes API error 500: boom - details")


class WebSocketProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_ProxyWebsocketMessages_UpstreamClose_ClosesBrowser(self) -> None:
        websocket = _FakeWebSocket(receive_messages=[])
        upstream = _FakeUpstream(messages=[])

        await code_proxy._proxy_websocket_messages(websocket, upstream)

        self.assertTrue(websocket.closed)
        self.assertTrue(websocket.receive_cancelled)

    async def test_ProxyWebsocketMessages_BrowserDisconnect_ClosesUpstream(self) -> None:
        websocket = _FakeWebSocket(receive_messages=[{"type": "websocket.disconnect"}])
        upstream = _FakeUpstream(wait_forever=True)

        await code_proxy._proxy_websocket_messages(websocket, upstream)

        self.assertFalse(websocket.closed)
        self.assertTrue(upstream.closed)

    async def test_UpstreamToBrowser_ForwardsTextAndBytes(self) -> None:
        websocket = _FakeWebSocket(receive_messages=[])
        upstream = _FakeUpstream(messages=["hello", b"world"])

        await code_proxy._upstream_to_browser(websocket, upstream)

        self.assertEqual(websocket.sent_text, ["hello"])
        self.assertEqual(websocket.sent_bytes, [b"world"])


class _FakeWebSocket:
    def __init__(self, receive_messages: list[dict[str, Any]]) -> None:
        self._receive_messages = receive_messages
        self.client_state = SimpleNamespace(name="CONNECTED")
        self.closed = False
        self.receive_cancelled = False
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []

    async def receive(self) -> dict[str, Any]:
        if self._receive_messages:
            return self._receive_messages.pop(0)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.receive_cancelled = True
            raise
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000) -> None:
        self.closed = True
        self.close_code = code
        self.client_state.name = "DISCONNECTED"

    async def send_text(self, message: str) -> None:
        self.sent_text.append(message)

    async def send_bytes(self, message: bytes) -> None:
        self.sent_bytes.append(message)


class _FakeUpstream:
    def __init__(self, messages: list[str | bytes] | None = None, wait_forever: bool = False) -> None:
        self._messages = list(messages or [])
        self._wait_forever = wait_forever
        self.closed = False
        self.sent: list[str | bytes] = []

    def __aiter__(self) -> "_FakeUpstream":
        return self

    async def __anext__(self) -> str | bytes:
        if self._messages:
            return self._messages.pop(0)
        if self._wait_forever:
            await asyncio.Event().wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.closed = True

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)


if __name__ == "__main__":
    unittest.main()
