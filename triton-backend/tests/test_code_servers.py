"""Unit tests for per-user code-server workspace behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.api import code_server_api
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.schemas import CreateCodeServerRequest
from app.services.code_server import kubernetes as k8s
from app.services.code_server import workspaces


class CodeServerTests(unittest.TestCase):
    def _request(self) -> CreateCodeServerRequest:
        return CreateCodeServerRequest(
            name="Dev Workspace",
            image="nvcr.io/nvidia/tritonserver:25.02-py3",
            password="super-secret",
            ingress_host="http://code.example.local",
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
            "password_enc": "super-secret",
            "status": "creating",
            "status_message": "Pending",
            "applied_resources": ["StatefulSet/code-7-dev-workspace"],
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_CreateCodeServerRequest_NormalizesNameAndIngress(self) -> None:
        request = self._request()

        self.assertEqual(request.name, "dev-workspace")
        self.assertEqual(request.ingress_host, "code.example.local")
        self.assertEqual(request.ingress_scheme, "http")

    def test_CreateCodeServerRequest_InvalidValues_RaiseValidationError(self) -> None:
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(name="!!!", image="triton", password="super-secret")
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(
                name="workspace",
                image=" ",
                password="super-secret",
            )
        with self.assertRaises(ValueError):
            CreateCodeServerRequest(
                name="workspace",
                image="triton",
                password="super-secret",
                ingress_host="https://code.example.local/path",
            )

    def test_Manifests_CreateStatefulSetWithPersistentWorkspaceAndPasswordSecret(self) -> None:
        request = self._request()

        manifests = k8s._manifests(
            request,
            "triton-control",
            "code-7-dev-workspace",
            "code-7-dev-workspace-svc",
            "code-7-dev-workspace-secret",
        )

        secret = manifests[0]
        statefulset = manifests[1]
        service = manifests[2]
        ingress = manifests[3]
        container = statefulset["spec"]["template"]["spec"]["containers"][0]

        self.assertEqual(secret["stringData"]["PASSWORD"], "super-secret")
        self.assertEqual(statefulset["kind"], "StatefulSet")
        self.assertEqual(container["image"], "nvcr.io/nvidia/tritonserver:25.02-py3")
        self.assertIn("code-server --bind-addr 0.0.0.0:8080", container["args"][0])
        self.assertEqual(
            statefulset["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"],
            "30Gi",
        )
        self.assertEqual(service["spec"]["ports"][0]["port"], 8080)
        self.assertEqual(ingress["spec"]["rules"][0]["host"], "code.example.local")

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
        statefulset = manifests[2]
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
            "app.services.code_server.workspaces.k8s.workspace_url",
            return_value="http://code.example.local",
        ), patch(
            "app.services.code_server.workspaces.k8s.apply_code_server_resources",
            return_value=["Secret/code-7-dev-workspace-secret", "StatefulSet/code-7-dev-workspace"],
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_by_owner_and_name",
            return_value=None,
        ), patch(
            "app.services.code_server.workspaces.code_servers.create",
            side_effect=create_row,
        ) as create:
            result = workspaces.create_code_server(self._request(), SimpleNamespace(), {"email": "u@example.com"})

        create.assert_called_once()
        self.assertEqual(result.id, 9)
        self.assertEqual(result.url, "http://code.example.local")
        self.assertEqual(created["owner_user_id"], 7)
        self.assertEqual(created["name"], "dev-workspace")
        self.assertEqual(created["statefulset_name"], "code-7-dev-workspace")

    def test_CreateCodeServer_ExistingWorkspace_UpdatesOwnedRecord(self) -> None:
        existing = self._row(status="ready", image="old-image")
        user = SimpleNamespace(id=7)

        with patch("app.services.code_server.workspaces.kubernetes_enabled", return_value=True), patch(
            "app.services.code_server.workspaces.require_user_entity",
            return_value=user,
        ), patch("app.services.code_server.workspaces._workspace_namespace", return_value="triton-control"), patch(
            "app.services.code_server.workspaces.k8s.workspace_url",
            return_value="http://code.example.local",
        ), patch(
            "app.services.code_server.workspaces.k8s.apply_code_server_resources",
            return_value=["StatefulSet/code-7-dev-workspace"],
        ), patch(
            "app.services.code_server.workspaces.code_servers.find_by_owner_and_name",
            return_value=existing,
        ), patch(
            "app.services.code_server.workspaces.code_servers.save",
            side_effect=lambda _session, row: row,
        ) as save:
            result = workspaces.create_code_server(self._request(), SimpleNamespace(), {"email": "u@example.com"})

        save.assert_called_once()
        self.assertEqual(existing.image, "nvcr.io/nvidia/tritonserver:25.02-py3")
        self.assertEqual(result.status, "creating")

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
            password_enc="pw123456",
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
        self.assertEqual(result[0].password, "pw123456")

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
            "StatefulSet/code-7-dev-workspace",
            "Service/code-7-dev-workspace-svc",
            "Ingress/code-7-dev-workspace-ingress",
        ])
        self.assertEqual(applied[1]["kind"], "StatefulSet")

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

    def test_ApiError_FormatsStatusReasonAndBody(self) -> None:
        message = k8s._api_error(SimpleNamespace(status=500, reason="boom", body="details"))
        self.assertEqual(message, "Kubernetes API error 500: boom - details")


if __name__ == "__main__":
    unittest.main()
