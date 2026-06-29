"""Unit tests for ``TritonService`` — the async HTTP client for Triton Inference Server.

Covers:
  ``is_ready`` / ``is_live``     — health-check endpoints.
  ``collect_runtime_snapshot``   — combined readiness / liveness / metadata fetch.
  ``get_model_names``            — server metadata model name extraction.
  ``get_repository_index``       — model repository index listing.
  ``get_model_config``           — per-model config retrieval.
  ``load_model`` / ``unload_model`` — lifecycle control.
  ``infer``                      — raw inference proxy.

All HTTP interactions are replaced with a ``FakeClient`` that replays
pre-configured responses without any network I/O.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.services.triton.client import TritonService


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", headers=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {}
        self._raises = raises

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self._raises or self.status_code >= 400:
            raise RuntimeError("http error")


class FakeClient:
    def __init__(self, get_responses=None, post_responses=None):
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []
        self.closed = False

    async def get(self, url):
        self.get_calls.append(url)
        result = self.get_responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        result = self.post_responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self):
        self.closed = True


class TritonServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await TritonService.close_all_clients()

    async def asyncTearDown(self):
        await TritonService.close_all_clients()

    async def test_GetClient_CacheContainsMatchingKey_ReturnsExistingClient(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient()
        service._clients[(service.triton_url, service.timeout, service.verify_cache_key, service.trust_env)] = client

        # Act
        resolved = service._get_client()

        # Assert
        self.assertIs(resolved, client)

    async def test_GetClient_DefaultAndEnvOverride_ControlsProxyTrust(self):
        # Arrange / Act / Assert
        with patch.dict("os.environ", {}, clear=True), patch(
            "app.services.triton.client.httpx.AsyncClient"
        ) as async_client:
            async_client.return_value.aclose = AsyncMock()
            service = TritonService("http://triton11-test.localtest.me")
            service._get_client()
            self.assertFalse(service.trust_env)
            self.assertFalse(async_client.call_args.kwargs["trust_env"])

        await TritonService.close_all_clients()

        with patch.dict("os.environ", {"TRITON_HTTP_TRUST_ENV": "true"}, clear=True), patch(
            "app.services.triton.client.httpx.AsyncClient"
        ) as async_client:
            async_client.return_value.aclose = AsyncMock()
            service = TritonService("http://triton.example.com")
            service._get_client()
            self.assertTrue(service.trust_env)
            self.assertTrue(async_client.call_args.kwargs["trust_env"])

    async def test_BuildVerify_CustomCertificate_UsesDefaultContextWithExtraCa(self):
        # Arrange / Act
        with patch(
            "app.services.triton.client.create_default_context_with_extra_ca",
            return_value="context",
        ) as build_context:
            service = TritonService(
                "https://triton",
                verify_ssl=True,
                ca_certificate="-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----",
            )

        # Assert
        self.assertEqual(service.verify, "context")
        build_context.assert_called_once_with(service.ca_certificate, "Triton")

    async def test_IsReady_DownstreamSuccessOrFailure_ReturnsExpectedBoolean(self):
        # Arrange
        service = TritonService("http://triton")
        service._get_client = lambda: FakeClient(get_responses=[FakeResponse(status_code=200)])

        # Act
        ready_result = await service.is_ready()

        # Assert
        self.assertTrue(ready_result)

        # Arrange
        service._get_client = lambda: FakeClient(get_responses=[RuntimeError("boom")])

        # Act
        error_result = await service.is_ready()

        # Assert
        self.assertFalse(error_result)

    async def test_CollectRuntimeSnapshot_HealthyEndpoints_ReturnsSnapshotWithoutError(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient(
            get_responses=[
                FakeResponse(status_code=200, payload={"name": "srv"}),
                FakeResponse(status_code=200),
                FakeResponse(status_code=200),
            ]
        )
        service._get_client = lambda: client

        # Act
        result = await service.collect_runtime_snapshot(include_metadata=True)

        # Assert
        self.assertEqual(result["metadata"], {"name": "srv"})
        self.assertTrue(result["live"])
        self.assertTrue(result["ready"])
        self.assertIsNone(result["error"])

    async def test_CollectRuntimeSnapshot_UnhealthyEndpoints_ReturnsAggregatedErrors(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient(
            get_responses=[
                FakeResponse(status_code=500, payload={}),
                RuntimeError("live down"),
                FakeResponse(status_code=503),
            ]
        )
        service._get_client = lambda: client

        # Act
        result = await service.collect_runtime_snapshot(include_metadata=True)

        # Assert
        self.assertFalse(result["live"])
        self.assertFalse(result["ready"])
        self.assertIn("v2 returned HTTP 500", result["error"])
        self.assertIn("live request failed", result["error"])
        self.assertIn("ready returned HTTP 503", result["error"])

    async def test_GetModelNames_Post405FallbackUsed_ReturnsReadyModelNames(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient(
            post_responses=[
                FakeResponse(status_code=405),
                FakeResponse(
                    status_code=200,
                    payload=[
                        {"name": "b", "state": "READY"},
                        {"name": "a", "state": "UNAVAILABLE"},
                        {"name": "c", "state": "READY"},
                    ],
                ),
            ]
        )
        service._get_client = lambda: client

        # Act
        names = await service.get_model_names()

        # Assert
        self.assertEqual(names, ["b", "c"])

    async def test_GetModelNames_InvalidPayloadOrException_ReturnsEmptyList(self):
        # Arrange
        service = TritonService("http://triton")
        service._get_client = lambda: FakeClient(post_responses=[FakeResponse(status_code=200, payload={"x": 1})])

        # Act
        invalid_payload_result = await service.get_model_names()

        # Assert
        self.assertEqual(invalid_payload_result, [])

        # Arrange
        service._get_client = lambda: FakeClient(post_responses=[RuntimeError("down")])

        # Act
        error_result = await service.get_model_names()

        # Assert
        self.assertEqual(error_result, [])

    async def test_GetRepositoryIndex_MixedPayloadItems_FiltersToDictRows(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient(
            post_responses=[FakeResponse(status_code=200, payload=[{"name": "ok"}, "x", 5, {"name": "also"}])]
        )
        service._get_client = lambda: client

        # Act
        rows = await service.get_repository_index()

        # Assert
        self.assertEqual(rows, [{"name": "ok"}, {"name": "also"}])

    async def test_CollectMetricsSnapshot_PrometheusMetrics_ReturnsUtilizationPercentages(self):
        # Arrange
        service = TritonService("http://triton")
        metrics = b"""
# HELP nv_cpu_utilization CPU utilization
nv_cpu_utilization 25
nv_cpu_memory_used_bytes 512
nv_cpu_memory_total_bytes 1024
nv_gpu_utilization{gpu_uuid="GPU-1"} 40
nv_gpu_utilization{gpu_uuid="GPU-2"} 60
"""
        client = FakeClient(get_responses=[FakeResponse(status_code=200, content=metrics)])
        service._get_client = lambda: client

        # Act
        result = await service.collect_metrics_snapshot("http://triton:8002/metrics")

        # Assert
        self.assertEqual(result["cpu"], 25)
        self.assertEqual(result["ram"], 50)
        self.assertEqual(result["gpu"], 50)
        self.assertIsNone(result["error"])

    async def test_CollectMetricsSnapshot_NoCpuRamFamilies_ReturnsDiagnosticError(self):
        # Arrange
        service = TritonService("http://triton")
        metrics = b"""
nv_inference_request_success 3
nv_gpu_utilization{gpu_uuid="GPU-1"} 40
"""
        client = FakeClient(get_responses=[FakeResponse(status_code=200, content=metrics)])
        service._get_client = lambda: client

        # Act
        result = await service.collect_metrics_snapshot("http://triton:8002/metrics")

        # Assert
        self.assertEqual(result["cpu"], 0)
        self.assertEqual(result["ram"], 0)
        self.assertEqual(result["gpu"], 40)
        self.assertIn("does not expose Triton CPU/RAM metrics", result["error"])

    async def test_InferenceMetricsDelta_EnsembleAndStepCounters_ReturnsLatencyRows(self):
        # Arrange
        service = TritonService("http://triton")
        before_metrics = b"""
nv_inference_request_success{model="ensemble",version="1"} 10
nv_inference_request_duration_us{model="ensemble",version="1"} 10000
nv_inference_queue_duration_us{model="ensemble",version="1"} 1000
nv_inference_compute_input_duration_us{model="ensemble",version="1"} 1000
nv_inference_compute_infer_duration_us{model="ensemble",version="1"} 7000
nv_inference_compute_output_duration_us{model="ensemble",version="1"} 1000
nv_inference_request_success{model="preprocess",version="1"} 5
nv_inference_request_duration_us{model="preprocess",version="1"} 5000
nv_inference_queue_duration_us{model="preprocess",version="1"} 500
nv_inference_compute_input_duration_us{model="preprocess",version="1"} 500
nv_inference_compute_infer_duration_us{model="preprocess",version="1"} 3500
nv_inference_compute_output_duration_us{model="preprocess",version="1"} 500
"""
        after_metrics = b"""
nv_inference_request_success{model="ensemble",version="1"} 11
nv_inference_request_duration_us{model="ensemble",version="1"} 15000
nv_inference_queue_duration_us{model="ensemble",version="1"} 1500
nv_inference_compute_input_duration_us{model="ensemble",version="1"} 1500
nv_inference_compute_infer_duration_us{model="ensemble",version="1"} 11000
nv_inference_compute_output_duration_us{model="ensemble",version="1"} 1500
nv_inference_request_success{model="preprocess",version="1"} 6
nv_inference_request_duration_us{model="preprocess",version="1"} 7000
nv_inference_queue_duration_us{model="preprocess",version="1"} 700
nv_inference_compute_input_duration_us{model="preprocess",version="1"} 700
nv_inference_compute_infer_duration_us{model="preprocess",version="1"} 4900
nv_inference_compute_output_duration_us{model="preprocess",version="1"} 700
"""
        client = FakeClient(
            get_responses=[
                FakeResponse(status_code=200, content=before_metrics),
                FakeResponse(status_code=200, content=after_metrics),
            ],
        )
        service._get_client = lambda: client

        # Act
        before = await service.collect_inference_metrics_snapshot("http://triton:8002/metrics")
        after = await service.collect_inference_metrics_snapshot("http://triton:8002/metrics")
        result = TritonService.inference_metrics_delta(before, after)

        # Assert
        self.assertTrue(result["available"])
        self.assertEqual([row["model"] for row in result["models"]], ["ensemble", "preprocess"])
        self.assertEqual(result["models"][0]["totalMs"], 5)
        self.assertEqual(result["models"][0]["queueMs"], 0.5)
        self.assertEqual(result["models"][0]["withoutQueueMs"], 4.5)
        self.assertEqual(result["models"][0]["computeInferMs"], 4)
        self.assertEqual(result["models"][1]["totalMs"], 2)
        self.assertEqual(result["models"][1]["computeInputMs"], 0.2)

    async def test_InferenceMetricsDelta_InferenceCountCounter_ReturnsLatencyRow(self):
        # Arrange
        before = {
            "series": {
                "m|1": {
                    "model": "m",
                    "version": "1",
                    "request_count": 7,
                    "total_us": 10000,
                    "queue_us": 1000,
                    "input_us": 1000,
                    "infer_us": 7000,
                    "output_us": 1000,
                }
            },
            "error": None,
        }
        after = {
            "series": {
                "m|1": {
                    "model": "m",
                    "version": "1",
                    "request_count": 8,
                    "total_us": 14000,
                    "queue_us": 1200,
                    "input_us": 1300,
                    "infer_us": 10500,
                    "output_us": 1000,
                }
            },
            "error": None,
        }

        # Act
        result = TritonService.inference_metrics_delta(before, after)

        # Assert
        self.assertTrue(result["available"])
        self.assertEqual(result["models"][0]["requestCount"], 1)
        self.assertEqual(result["models"][0]["totalMs"], 4)
        self.assertEqual(result["models"][0]["queueMs"], 0.2)

    async def test_InferenceMetricsDelta_DurationOnlyAfterFirstRequest_ReturnsLatencyRow(self):
        # Arrange
        before: dict[str, object] = {"series": {}, "error": None}
        after = {
            "series": {
                "simple_dyna_sequence|1": {
                    "model": "simple_dyna_sequence",
                    "version": "1",
                    "request_count": 0,
                    "total_us": 35205,
                    "queue_us": 32082,
                    "input_us": 0,
                    "infer_us": 0,
                    "output_us": 0,
                }
            },
            "error": None,
        }

        # Act
        result = TritonService.inference_metrics_delta(before, after)

        # Assert
        self.assertTrue(result["available"])
        self.assertEqual(result["models"][0]["model"], "simple_dyna_sequence")
        self.assertEqual(result["models"][0]["totalMs"], 35.205)
        self.assertEqual(result["models"][0]["queueMs"], 32.082)
        self.assertEqual(result["models"][0]["withoutQueueMs"], 3.123)

    async def test_ParseInferenceMetricSeries_InferenceCountFamily_UsedAsRequestCount(self):
        # Arrange
        metrics = """
nv_inference_count{model="m",version="1"} 12
nv_inference_request_duration_us{model="m",version="1"} 12000
"""

        # Act
        result = TritonService._parse_inference_metric_series(metrics)

        # Assert
        self.assertEqual(result["m|1"]["request_count"], 12)
        self.assertEqual(result["m|1"]["total_us"], 12000)

    async def test_ParseInferenceStatsSeries_ModelStatsPayload_ReturnsLatencyCounters(self):
        # Arrange
        payload = {
            "model_stats": [
                {
                    "name": "ensemble",
                    "version": "1",
                    "inference_count": 4,
                    "inference_stats": {
                        "success": {"count": 4, "ns": 12000000},
                        "queue": {"count": 4, "ns": 1000000},
                        "compute_input": {"count": 4, "ns": 2000000},
                        "compute_infer": {"count": 4, "ns": 8000000},
                        "compute_output": {"count": 4, "ns": 1000000},
                    },
                }
            ]
        }

        # Act
        result = TritonService._parse_inference_stats_series(payload)

        # Assert
        self.assertEqual(result["ensemble|1"]["request_count"], 4)
        self.assertEqual(result["ensemble|1"]["total_us"], 12000)
        self.assertEqual(result["ensemble|1"]["queue_us"], 1000)
        self.assertEqual(result["ensemble|1"]["infer_us"], 8000)

    async def test_ModelActionsAndInfer_ModelAndVersionNeedEncoding_UsesEncodedUrls(self):
        # Arrange
        service = TritonService("http://triton")
        client = FakeClient(
            get_responses=[FakeResponse(status_code=200, payload={"cfg": 1})],
            post_responses=[
                FakeResponse(status_code=200),  # load
                FakeResponse(status_code=200),  # unload
                FakeResponse(
                    status_code=200,
                    payload={"ok": True},
                    content=b'{"ok":true}',
                    headers={"content-type": "application/json"},
                ),  # infer
                FakeResponse(status_code=200, payload={"ok": True}),  # infer raw
                FakeResponse(status_code=200, payload={"text_output": "ok"}),  # generate raw
            ],
        )
        service._get_client = lambda: client

        # Act
        cfg = await service.get_model_config("model a", "1/2")
        await service.load_model("model a")
        await service.unload_model("model a")
        infer_resp = await service.infer_model("model a", "1/2", {"x": 1})
        raw_resp = await service.infer_model_raw("model a", "1/2", b"{}", "application/json")
        generate_resp = await service.generate_model_raw("model a", b'{"text_input":"hi"}', "application/json")

        # Assert
        self.assertEqual(cfg, {"cfg": 1})
        self.assertIn("/models/model%20a/versions/1%2F2/config", client.get_calls[0])
        self.assertEqual(infer_resp.status_code, 200)
        self.assertEqual(raw_resp.status_code, 200)
        self.assertEqual(generate_resp.status_code, 200)
        self.assertIn("/repository/models/model%20a/load", client.post_calls[0][0])
        self.assertIn("/repository/models/model%20a/unload", client.post_calls[1][0])
        self.assertIn("/models/model%20a/versions/1%2F2/infer", client.post_calls[2][0])
        self.assertEqual(client.post_calls[3][1]["headers"]["content-type"], "application/json")
        self.assertIn("/models/model%20a/generate", client.post_calls[4][0])
        self.assertEqual(client.post_calls[4][1]["headers"]["content-type"], "application/json")


if __name__ == "__main__":
    unittest.main()
