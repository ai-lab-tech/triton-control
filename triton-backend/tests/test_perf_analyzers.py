"""Unit tests for Kubernetes Perf Analyzer installation behavior."""

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import ANY, patch

from fastapi import HTTPException

from app.api.perf_analyzer_api import install_perf_analyzer as install_perf_analyzer_api
from app.exceptions import BadRequestError, ConflictError
from app.repositories import perf_analyzer as perf_repo
from app.schemas import InstallPerfAnalyzerRequest, RunPerfAnalyzerRequest
from app.services.perf_analyzer import installer
from app.services.perf_analyzer import kubernetes as k8s


class PerfAnalyzerInstallTests(unittest.TestCase):
    def _request(self) -> InstallPerfAnalyzerRequest:
        return InstallPerfAnalyzerRequest(
            installation_name="perf analyzer",
            image="nvcr.io/nvidia/tritonserver:25.02-py3-sdk",
        )

    def test_InstallPerfAnalyzer_NameProvided_AppliesNamedResources(self) -> None:
        with patch("app.services.perf_analyzer.installer.perf_analyzer.get", return_value=None), patch(
            "app.services.perf_analyzer.installer.k8s.apply_installation_resources",
            return_value=["Deployment/perf-analyzer"],
        ) as apply_resources, patch(
            "app.services.perf_analyzer.installer.perf_analyzer.save",
            side_effect=lambda _session, entity: entity,
        ):
            response = installer.install_perf_analyzer(self._request(), SimpleNamespace())

        apply_resources.assert_called_once()
        self.assertEqual(apply_resources.call_args.kwargs["namespace"], "perf-analyzer")
        self.assertEqual(response.namespace, "perf-analyzer")
        self.assertEqual(response.deployment_name, "perf-analyzer")
        self.assertEqual(response.applied_resources, ["Deployment/perf-analyzer"])

    def test_Manifests_DockerConfigProvided_AddsImagePullSecret(self) -> None:
        dockerconfigjson = '{"auths":{"registry.example":{"auth":"token"}}}'
        request = self._request().model_copy(update={"dockerconfigjson": dockerconfigjson})

        manifests = k8s._manifests(request, "perf-analyzer", "perf-analyzer")

        secret = manifests[0]
        deployment = manifests[1]
        self.assertEqual(secret["metadata"]["name"], "perf-analyzer-pull-secret")
        self.assertEqual(secret["stringData"][".dockerconfigjson"], dockerconfigjson)
        self.assertEqual(
            deployment["spec"]["template"]["spec"]["imagePullSecrets"],
            [{"name": "perf-analyzer-pull-secret"}],
        )
        container = deployment["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], "nvcr.io/nvidia/tritonserver:25.02-py3-sdk")
        self.assertEqual(container["command"], ["/bin/bash", "-c"])
        self.assertIn("sleep infinity", container["args"][0])

    def test_ApplyInstallationResources_AppliesManifestObjects(self) -> None:
        applied: list[dict[str, Any]] = []

        with patch("app.services.perf_analyzer.kubernetes._client", return_value=object()), patch(
            "app.services.perf_analyzer.kubernetes._ensure_namespace"
        ) as ensure_namespace, patch(
            "kubernetes.utils.create_from_dict",
            side_effect=lambda _api_client, data, **_kwargs: applied.append(data),
        ), patch(
            "app.services.perf_analyzer.kubernetes._wait_for_running_pod"
        ):
            resources = k8s.apply_installation_resources(
                self._request(),
                namespace="perf-analyzer",
                deployment_name="perf-analyzer",
            )

        ensure_namespace.assert_called_once()
        self.assertEqual(applied[0]["kind"], "Deployment")
        self.assertEqual(resources, ["Deployment/perf-analyzer"])

    def test_WaitForRunningPod_RunningPhaseReturned_StopsWaiting(self) -> None:
        pod = SimpleNamespace(
            metadata=SimpleNamespace(name="perf-analyzer-abc"),
            status=SimpleNamespace(phase="Running"),
        )

        with patch("kubernetes.client.CoreV1Api") as core_api, patch(
            "app.services.perf_analyzer.kubernetes.time.sleep"
        ) as sleep:
            core_api.return_value.list_namespaced_pod.return_value = SimpleNamespace(items=[pod])

            k8s._wait_for_running_pod(object(), "perf-analyzer")

        sleep.assert_not_called()

    def test_InstallPerfAnalyzerApi_ServiceRaisesDomainError_ReturnsHttpError(self) -> None:
        with patch(
            "app.services.perf_analyzer.installer.install_perf_analyzer",
            side_effect=BadRequestError("bad perf analyzer request"),
        ):
            with self.assertRaises(HTTPException) as raised:
                install_perf_analyzer_api(self._request(), session=SimpleNamespace(), _claims=SimpleNamespace())

        self.assertEqual(raised.exception.status_code, 400)

    def test_UninstallPerfAnalyzer_RecordExists_DeletesNamespaceAndRecord(self) -> None:
        entity = SimpleNamespace(namespace="perf")

        with patch("app.services.perf_analyzer.installer.perf_analyzer.get", return_value=entity), patch(
            "app.services.perf_analyzer.installer.perf_analyzer.save",
            side_effect=lambda _session, saved_entity: saved_entity,
        ), patch(
            "app.services.perf_analyzer.installer.k8s.delete_namespace",
            return_value="Namespace 'perf' deletion requested.",
        ) as delete_namespace, patch("app.services.perf_analyzer.installer.perf_analyzer.delete") as delete_record:
            response = installer.uninstall_perf_analyzer(SimpleNamespace())

        delete_namespace.assert_called_once_with("perf")
        delete_record.assert_called_once()
        self.assertEqual(response.status, "deleted")

    def test_RunPerfAnalyzer_InstalledPod_ExecutesModelCommand(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1:4:1",
        )
        entity = SimpleNamespace(namespace="perf")
        instance = SimpleNamespace(url="http://triton.example.local", is_self_deployed=False)

        with patch("app.services.perf_analyzer.installer.perf_analyzer.get", return_value=entity), patch(
            "app.services.perf_analyzer.installer.get_instance_or_404",
            return_value=instance,
        ), patch(
            "app.services.perf_analyzer.installer.k8s.exec_running_pod",
            return_value="perf output",
        ) as exec_pod, patch(
            "app.services.perf_analyzer.installer.perf_analyzer.get_latest_run",
            return_value=None,
        ), patch(
            "app.services.perf_analyzer.installer.perf_analyzer.save_latest_run",
            side_effect=lambda _session, run: run,
        ) as save_latest_run:
            response = installer.run_perf_analyzer(request, SimpleNamespace(), {"role": "admin"})

        self.assertEqual(response.output, "perf output")
        self.assertIn("resnet", response.command)
        self.assertIn("triton.example.local", response.command)
        exec_pod.assert_called_once()
        save_latest_run.assert_called_once()
        saved_run = save_latest_run.call_args.args[1]
        self.assertEqual(saved_run.instance_id, 3)
        self.assertEqual(saved_run.model_name, "resnet")
        self.assertEqual(saved_run.model_version, "1")
        self.assertEqual(saved_run.output, "perf output")

    def test_GetLatestPerfAnalyzerRun_RecordExists_ReturnsPersistedResult(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )
        run = SimpleNamespace(
            executed_at=None,
            batch_size=2,
            concurrency_range="1:4:1",
            measurement_request_count=80,
            input_data="{}",
            command=["perf_analyzer"],
            output="previous output",
        )

        with patch("app.services.perf_analyzer.installer.get_instance_or_404"), patch(
            "app.services.perf_analyzer.installer.perf_analyzer.get_latest_run",
            return_value=run,
        ) as get_latest_run:
            response = installer.get_latest_perf_analyzer_run(request, SimpleNamespace(), {"role": "admin"})

        get_latest_run.assert_called_once_with(
            ANY,
            instance_id=3,
            model_name="resnet",
            model_version="1",
        )
        self.assertTrue(response.found)
        self.assertEqual(response.output, "previous output")
        self.assertEqual(response.batch_size, 2)

    def test_GetLatestPerfAnalyzerRun_NoRecord_ReturnsNotFoundPayload(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )

        with patch("app.services.perf_analyzer.installer.get_instance_or_404"), patch(
            "app.services.perf_analyzer.installer.perf_analyzer.get_latest_run",
            return_value=None,
        ):
            response = installer.get_latest_perf_analyzer_run(request, SimpleNamespace(), {"role": "admin"})

        self.assertFalse(response.found)
        self.assertEqual(response.output, "")

    def test_RunPerfAnalyzer_AnotherRunInProgress_RaisesConflict(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )

        acquired = installer._run_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(ConflictError) as raised:
                installer.run_perf_analyzer(request, SimpleNamespace(), {"role": "admin"})
        finally:
            installer._run_lock.release()

        self.assertEqual(raised.exception.detail, "Another Perf Analyzer run is already in progress")

    def test_RunPerfAnalyzer_RunFails_ReleasesGlobalLock(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )

        with patch("app.services.perf_analyzer.installer.perf_analyzer.get", side_effect=BadRequestError("boom")):
            with self.assertRaises(BadRequestError):
                installer.run_perf_analyzer(request, SimpleNamespace(), {"role": "admin"})

        acquired = installer._run_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        if acquired:
            installer._run_lock.release()

    def test_PerfAnalyzerTarget_SelfDeployedSameNamespaceWithIngress_UsesExternalUrl(self) -> None:
        instance = SimpleNamespace(
            url="https://triton.example.local",
            is_self_deployed=True,
            deployment_service_name="triton-admin-service",
            deployment_namespace="triton-admin",
        )

        target = installer._perf_analyzer_target(
            instance,
            perf_analyzer_namespace="triton-admin",
        )

        self.assertEqual(target, "triton.example.local")

    def test_PerfAnalyzerTarget_SelfDeployedDifferentNamespace_UsesExternalUrl(self) -> None:
        instance = SimpleNamespace(
            url="https://triton.example.local",
            is_self_deployed=True,
            deployment_service_name="triton-admin-service",
            deployment_namespace="triton-admin",
        )

        target = installer._perf_analyzer_target(
            instance,
            perf_analyzer_namespace="perf-analyzer",
        )

        self.assertEqual(target, "triton.example.local")

    def test_PerfAnalyzerTarget_ExternalInstance_UsesRegisteredTritonUrl(self) -> None:
        instance = SimpleNamespace(url="https://triton.example.local/triton", is_self_deployed=False)

        target = installer._perf_analyzer_target(
            instance,
            perf_analyzer_namespace="perf-analyzer",
        )

        self.assertEqual(target, "triton.example.local")

    def test_PerfAnalyzerTarget_SameNamespaceWithServiceUrl_UsesInternalTritonService(self) -> None:
        instance = SimpleNamespace(
            url="http://triton-admin-service.triton-admin.svc.cluster.local:18000",
            is_self_deployed=False,
            deployment_service_name="triton-admin-service",
            deployment_namespace="triton-admin",
        )

        target = installer._perf_analyzer_target(
            instance,
            perf_analyzer_namespace="triton-admin",
        )

        self.assertEqual(target, "triton-admin-service.triton-admin.svc.cluster.local:18000")

    def test_PerfAnalyzerTarget_SameNamespaceWithServiceUrl_PrefersSavedHostPort(self) -> None:
        instance = SimpleNamespace(
            url="http://test-triton-service.triton-control.svc.cluster.local:18000/v2",
            is_self_deployed=True,
            deployment_service_name="different-service-name",
            deployment_namespace="triton-control",
        )

        target = installer._perf_analyzer_target(
            instance,
            perf_analyzer_namespace="triton-control",
        )

        self.assertEqual(target, "test-triton-service.triton-control.svc.cluster.local:18000")

    def test_PerfAnalyzerProtocol_ExternalHttpsInstance_UsesHttpTransportFlag(self) -> None:
        instance = SimpleNamespace(url="https://triton.example.local:8443", is_self_deployed=False)

        protocol = installer._perf_analyzer_protocol(instance, target="triton.example.local:8443")

        self.assertEqual(protocol, "HTTP")

    def test_RunCommand_ExternalHttpsInstance_UsesHttpProtocolFlag(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )
        instance = SimpleNamespace(url="https://triton.example.local:8443", is_self_deployed=False)

        command = installer._run_command(request, instance, perf_analyzer_namespace="perf")

        self.assertIn("-i", command)
        self.assertEqual(command[command.index("-i") + 1], "HTTP")

    def test_RunCommand_SameNamespaceWithIngressUrl_UsesHttpProtocolFlag(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )
        instance = SimpleNamespace(
            url="https://triton.example.local",
            is_self_deployed=True,
            deployment_service_name="triton-admin-service",
            deployment_namespace="triton-admin",
        )

        command = installer._run_command(request, instance, perf_analyzer_namespace="triton-admin")

        self.assertIn("-i", command)
        self.assertEqual(command[command.index("-i") + 1], "HTTP")

    def test_RunCommand_SelfDeployedInstance_UsesHttpProtocolFlag(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )
        instance = SimpleNamespace(
            url="http://triton-admin-service.triton-admin.svc.cluster.local:18000",
            is_self_deployed=True,
            deployment_service_name="triton-admin-service",
            deployment_namespace="triton-admin",
        )

        command = installer._run_command(request, instance, perf_analyzer_namespace="triton-admin")

        self.assertIn("-i", command)
        self.assertEqual(command[command.index("-i") + 1], "HTTP")

    def test_DirectPerfInputArgument_ModeAndPath_ReturnsDirectArgument(self) -> None:
        self.assertEqual(installer._direct_perf_input_argument("zero"), "zero")
        self.assertEqual(installer._direct_perf_input_argument("random"), "random")
        self.assertEqual(installer._direct_perf_input_argument("/tmp/pa_input.json"), "/tmp/pa_input.json")
        self.assertIsNone(installer._direct_perf_input_argument('{"data":[]}'))

    def test_RunCommand_InputDataArgument_UsesProvidedArgument(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="resnet",
            model_version="1",
            concurrency_range="1",
        )
        instance = SimpleNamespace(url="http://triton.example.local:8000", is_self_deployed=False)

        command = installer._run_command(
            request,
            instance,
            perf_analyzer_namespace="perf",
            input_data_arg="/tmp/pa_input.json",
        )

        self.assertIn("--input-data", command)
        self.assertEqual(command[command.index("--input-data") + 1], "/tmp/pa_input.json")

    def test_PerfAnalyzerRepository_SaveGetDeleteAndRunHelpers_UseSession(self) -> None:
        class _ExecResult:
            def __init__(self, row=None):
                self.row = row

            def first(self):
                return self.row

        class _Session:
            def __init__(self):
                self.entity = SimpleNamespace(id=perf_repo.PERF_ANALYZER_ID)
                self.run = SimpleNamespace(instance_id=7, model_name="m", model_version="1")
                self.added = []
                self.deleted = []
                self.exec_calls = []
                self.refresh_calls = []
                self.commit_count = 0

            def get(self, _model, key):
                return self.entity if key == perf_repo.PERF_ANALYZER_ID else None

            def add(self, entity):
                self.added.append(entity)

            def commit(self):
                self.commit_count += 1

            def refresh(self, entity):
                self.refresh_calls.append(entity)

            def delete(self, entity):
                self.deleted.append(entity)

            def exec(self, statement):
                self.exec_calls.append(statement)
                return _ExecResult(self.run)

        session = _Session()
        entity = SimpleNamespace()
        run = SimpleNamespace()

        self.assertIs(perf_repo.get(session), session.entity)
        self.assertIs(perf_repo.save(session, entity), entity)
        self.assertIs(perf_repo.save(session, entity, refresh=False), entity)
        perf_repo.delete(session, entity)
        self.assertIs(
            perf_repo.get_latest_run(session, instance_id=7, model_name="m", model_version="1"),
            session.run,
        )
        self.assertIs(perf_repo.save_latest_run(session, run), run)
        perf_repo.delete_runs_for_instance(session, 7)

        self.assertEqual(session.added, [entity, entity, run])
        self.assertEqual(session.deleted, [entity])
        self.assertEqual(session.refresh_calls, [entity, run])
        self.assertEqual(len(session.exec_calls), 2)
        self.assertEqual(session.commit_count, 5)
