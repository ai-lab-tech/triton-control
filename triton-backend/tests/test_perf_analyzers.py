"""Unit tests for Kubernetes Perf Analyzer installation behavior."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, patch

from app.repositories import perf_analyzer as perf_repo
from app.schemas import RunPerfAnalyzerRequest
from app.services.perf_analyzer import installer


class PerfAnalyzerCommandTests(unittest.TestCase):
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

        command = installer._run_command(request, instance, perf_analyzer_namespace="perf", decoupled=False)

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

        command = installer._run_command(request, instance, perf_analyzer_namespace="triton-admin", decoupled=False)

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

        command = installer._run_command(request, instance, perf_analyzer_namespace="triton-admin", decoupled=False)

        self.assertIn("-i", command)
        self.assertEqual(command[command.index("-i") + 1], "HTTP")

    def test_RunCommand_VllmDeployment_UsesGrpcAsyncStreaming(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=3,
            model_name="opt125m",
            model_version="1",
            batch_size=4,
            concurrency_range="1",
        )
        instance = SimpleNamespace(
            url="http://opt125m-service.triton-control.svc.cluster.local:18000",
            is_self_deployed=True,
            deployment_service_name="opt125m-service",
            deployment_namespace="triton-control",
            deployment_log="Image: nvcr.io/nvidia/tritonserver:26.06-py3",
        )

        with patch(
            "app.services.perf_analyzer.installer._fetch_triton_model_config",
            return_value={"backend": "vllm"},
        ):
            command = installer._run_command(request, instance, perf_analyzer_namespace="triton-control")

        self.assertEqual(command[command.index("-u") + 1], "opt125m-service.triton-control.svc.cluster.local:18001")
        self.assertEqual(command[command.index("-i") + 1], "grpc")
        self.assertEqual(command[command.index("-b") + 1], "1")
        self.assertIn("--async", command)
        self.assertIn("--streaming", command)

    def test_RunCommand_TensorRtLlmDeployment_DoesNotAssumeDecoupledMode(self) -> None:
        request = RunPerfAnalyzerRequest(
            instance_id=4,
            model_name="tensorrt_llm",
            model_version="1",
            concurrency_range="1",
        )
        instance = SimpleNamespace(
            url="http://trtllm-service.triton-control.svc.cluster.local:18000",
            is_self_deployed=True,
            deployment_service_name="trtllm-service",
            deployment_namespace="triton-control",
            deployment_log="Image: nvcr.io/nvidia/tritonserver:25.12-trtllm-python-py3",
        )

        command = installer._run_command(request, instance, perf_analyzer_namespace="triton-control", decoupled=False)

        self.assertEqual(command[command.index("-u") + 1], "trtllm-service.triton-control.svc.cluster.local:18000")
        self.assertEqual(command[command.index("-i") + 1], "HTTP")
        self.assertNotIn("--async", command)
        self.assertNotIn("--streaming", command)

    def test_DirectPerfInputArgument_ModeAndPath_ReturnsDirectArgument(self) -> None:
        self.assertEqual(installer._direct_perf_input_argument("zero"), "zero")
        self.assertEqual(installer._direct_perf_input_argument("random"), "random")
        self.assertEqual(installer._direct_perf_input_argument("/tmp/pa_input.json"), "/tmp/pa_input.json")
        self.assertIsNone(installer._direct_perf_input_argument('{"data":[]}'))

    def test_PrepareInputData_DecoupledVllmGeneratePayload_ConvertsToPerfAnalyzerData(self) -> None:
        raw = (
            '{"text_input":"Reason step by step","parameters":'
            '{"stream":false,"max_tokens":512,"temperature":0.6,"top_p":0.95}}'
        )

        prepared = installer._prepare_input_data_for_perf_analyzer(raw, decoupled=True)

        self.assertIsNotNone(prepared)
        parsed = json.loads(prepared or "{}")
        item = parsed["data"][0]
        self.assertEqual(item["text_input"], ["Reason step by step"])
        self.assertEqual(item["stream"], [True])
        sampling = json.loads(item["sampling_parameters"][0])
        self.assertEqual(sampling["max_tokens"], 512)
        self.assertEqual(sampling["temperature"], 0.6)
        self.assertEqual(sampling["top_p"], 0.95)
        self.assertNotIn("stream", sampling)

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
            decoupled=False,
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
