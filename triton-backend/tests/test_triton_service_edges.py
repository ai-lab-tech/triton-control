"""Focused unit tests for Triton service edge cases and helper branches."""

import asyncio
import base64
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.protobuf import text_format

from app.exceptions import BadRequestError, ConflictError, NotFoundError, ServiceUnavailableError
from app.schemas import CreateTritonInstanceRequest, UpdateTritonInstanceRequest
from app.services.triton import config as triton_config
from app.services.triton import instances as triton_instances
from app.services.triton import models as triton_models


class TritonConfigEdgeTests(unittest.TestCase):
    def test_ValidateTritonConfigPbtxt_InvalidUtf8_RaisesUnsupportedMediaType(self):
        # Act / Assert
        with self.assertRaises(Exception) as raised:
            triton_config.validate_triton_config_pbtxt(b"\xff", "2.55.0")

        self.assertIn("valid UTF-8", str(raised.exception))

    def test_ValidateTritonConfigPbtxt_UnknownVersion_RaisesUnprocessable(self):
        # Act / Assert
        with self.assertRaises(Exception) as raised:
            triton_config.validate_triton_config_pbtxt(b'name: "m"', "0.0.0")

        self.assertIn("Unable to map Triton version", str(raised.exception))

    def test_ValidateTritonConfigPbtxt_ParseError_AddsLineHint(self):
        # Arrange
        class _FakeConfig:
            pass

        def _raise_parse_error(_text, _message):
            raise text_format.ParseError("2:3 broken")

        content = b'name: "m"\ninput ['

        # Act / Assert
        with patch("app.services.triton.config._load_model_config_class", return_value=_FakeConfig), patch(
            "app.services.triton.config.text_format.Parse",
            side_effect=_raise_parse_error,
        ):
            with self.assertRaises(Exception) as raised:
                triton_config.validate_triton_config_pbtxt(content, "25.02")

        message = str(raised.exception)
        self.assertIn("Invalid Triton config.pbtxt", message)
        self.assertIn("Line 2: input [", message)
        self.assertIn("Likely missing closing square bracket", message)

    def test_ValidateTritonConfigPbtxt_KnownRelease_LoadsBundledProto(self):
        # Act / Assert
        triton_config.validate_triton_config_pbtxt(b'name: "m"\nbackend: "python"\n', "25.02")

    def test_LoadModelConfigClass_MissingProto_RaisesUsefulError(self):
        # Act / Assert
        with patch("app.services.triton.config.PROTOBUFF_DIR", triton_config.Path("missing-protos")):
            with self.assertRaises(Exception) as raised:
                triton_config._load_model_config_class("r99.99")

        self.assertIn("Missing model_config.proto", str(raised.exception))

    def test_ExtractTritonVersion_InvalidMetadata_ReturnsNone(self):
        # Act / Assert
        self.assertIsNone(triton_config.extract_triton_version(None))
        self.assertIsNone(triton_config.extract_triton_version({"version": "  "}))


class TritonInstanceEdgeTests(unittest.IsolatedAsyncioTestCase):
    def test_TritonConnectionValidationTimeout_DefaultAndEnvOverride(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(triton_instances.triton_connection_validation_timeout_seconds(), 5.0)

        with patch.dict("os.environ", {"TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS": "2.5"}):
            self.assertEqual(triton_instances.triton_connection_validation_timeout_seconds(), 2.5)

    def test_TritonConnectionValidationTimeout_InvalidValue_UsesDefault(self):
        for raw in ("bad", "0", "-1"):
            with self.subTest(raw=raw):
                with patch.dict("os.environ", {"TRITON_CONNECTION_VALIDATION_TIMEOUT_SECONDS": raw}):
                    self.assertEqual(triton_instances.triton_connection_validation_timeout_seconds(), 5.0)

    async def test_CollectRuntimeSnapshotWithTimeout_SlowValidation_ReturnsDownSnapshot(self):
        # Arrange
        service = SimpleNamespace(collect_runtime_snapshot=AsyncMock(side_effect=asyncio.TimeoutError))

        # Act
        snapshot = await triton_instances._collect_runtime_snapshot_with_timeout(
            service,
            "http://triton",
            0.01,
        )

        # Assert
        self.assertFalse(snapshot["live"])
        self.assertFalse(snapshot["ready"])
        self.assertIn("validation timed out", snapshot["error"])

    async def test_CreateInstance_TritonServiceInitFails_RaisesBadRequest(self):
        # Arrange
        request = CreateTritonInstanceRequest(url="http://bad")

        # Act / Assert
        with patch("app.services.triton.instances.TritonService", side_effect=RuntimeError("bad URL")):
            with self.assertRaises(BadRequestError):
                await triton_instances.create_instance(request, SimpleNamespace(), {"role": "admin"})

    async def test_CreateInstance_TritonNotReady_RaisesServiceUnavailable(self):
        # Arrange
        request = CreateTritonInstanceRequest(url="http://triton")
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {},
                    "live": True,
                    "ready": False,
                    "checked_at": datetime.now(timezone.utc),
                    "error": "not ready",
                }
            )
        )

        # Act / Assert
        with patch("app.services.triton.instances.TritonService", return_value=service):
            with self.assertRaises(Exception) as raised:
                await triton_instances.create_instance(request, SimpleNamespace(), {"role": "admin"})

        self.assertIn("not ready", str(raised.exception))

    async def test_UpdateInstance_DuplicateUrl_RaisesConflictBeforeConnecting(self):
        # Arrange
        request = UpdateTritonInstanceRequest(url="http://duplicate")
        instance = SimpleNamespace(id=1, name="a")
        duplicate = SimpleNamespace(id=2)

        # Act / Assert
        with patch("app.services.triton.instances.get_instance_or_404", return_value=instance), patch(
            "app.services.triton.instances.instances.find_by_url",
            return_value=duplicate,
        ):
            with self.assertRaises(ConflictError):
                await triton_instances.update_instance(request, SimpleNamespace(), {"role": "admin"}, 1)

    async def test_UpdateInstance_SelfDeployedInstance_PreservesDeploymentState(self):
        # Arrange
        request = UpdateTritonInstanceRequest(url="http://edited-triton:8000")
        instance = SimpleNamespace(
            id=1,
            name="gpu-a",
            url="http://old-triton:8000",
            model_names=[],
            repository_models=[],
            server_metadata={},
            health_live=False,
            health_ready=False,
            health_last_checked_at=None,
            health_error="",
            triton_verify_ssl=False,
            triton_ca_certificate="",
            metrics_url=None,
            metrics_cpu=0.0,
            metrics_ram=0.0,
            metrics_gpu=0.0,
            metrics_last_checked_at=None,
            metrics_error="",
            deployment_runtime="kubernetes",
            deployment_namespace="gpu-a",
            deployment_name="gpu-a",
            deployment_service_name="gpu-a-service",
            deployment_secret_name="gpu-a-s3",
            deployment_log="created",
            is_self_deployed=True,
            pod_statuses=["gpu-a: Pending"],
        )
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {"name": "triton"},
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            collect_metrics_snapshot=AsyncMock(
                return_value={
                    "cpu": 0.0,
                    "ram": 0.0,
                    "gpu": 0.0,
                    "checked_at": None,
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "m1", "version": "1", "state": "READY"}]),
        )

        # Act
        with patch("app.services.triton.instances.get_instance_or_404", return_value=instance), patch(
            "app.services.triton.instances.instances.find_by_url",
            return_value=None,
        ), patch("app.services.triton.instances.TritonService", return_value=service), patch(
            "app.services.triton.instances.instances.save",
            return_value=instance,
        ), patch(
            "app.services.triton.instances.entity_to_dto",
            return_value=instance,
        ):
            result = await triton_instances.update_instance(request, SimpleNamespace(), {"role": "admin"}, 1)

        # Assert
        self.assertIs(result, instance)
        self.assertEqual(instance.url, "http://edited-triton:8000")
        self.assertEqual(instance.deployment_runtime, "kubernetes")
        self.assertEqual(instance.deployment_namespace, "gpu-a")
        self.assertEqual(instance.deployment_name, "gpu-a")
        self.assertEqual(instance.deployment_service_name, "gpu-a-service")
        self.assertEqual(instance.deployment_secret_name, "gpu-a-s3")
        self.assertEqual(instance.deployment_log, "created")
        self.assertTrue(instance.is_self_deployed)
        self.assertEqual(instance.pod_statuses, ["gpu-a: Pending"])

    async def test_UpdateInstance_TritonNotReady_SavesUrlAndHealthSnapshot(self):
        # Arrange
        request = UpdateTritonInstanceRequest(url="http://test20.localtest.me")
        instance = SimpleNamespace(
            id=1,
            name="gpu-a",
            url="http://old-triton:8000",
            model_names=["old-model"],
            repository_models=[{"name": "old-model"}],
            server_metadata={"name": "old"},
            health_live=True,
            health_ready=True,
            health_last_checked_at=None,
            health_error="",
            triton_verify_ssl=False,
            triton_ca_certificate="",
            metrics_url=None,
            metrics_cpu=1.0,
            metrics_ram=2.0,
            metrics_gpu=3.0,
            metrics_last_checked_at=None,
            metrics_error="",
            deployment_runtime="external",
            deployment_namespace=None,
            deployment_name=None,
            deployment_service_name=None,
            deployment_secret_name=None,
            deployment_log="",
            is_self_deployed=False,
            pod_statuses=[],
        )
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": False,
                    "ready": False,
                    "checked_at": datetime.now(timezone.utc),
                    "error": "v2 returned HTTP 503; live returned HTTP 503; ready returned HTTP 503",
                }
            ),
            collect_metrics_snapshot=AsyncMock(
                return_value={
                    "cpu": 0.0,
                    "ram": 0.0,
                    "gpu": 0.0,
                    "checked_at": None,
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "should-not-load"}]),
        )

        # Act / Assert
        with patch("app.services.triton.instances.get_instance_or_404", return_value=instance), patch(
            "app.services.triton.instances.instances.find_by_url",
            return_value=None,
        ), patch("app.services.triton.instances.TritonService", return_value=service), patch(
            "app.services.triton.instances.instances.save",
            return_value=instance,
        ) as save:
            with self.assertRaises(ServiceUnavailableError) as raised:
                await triton_instances.update_instance(request, SimpleNamespace(), {"role": "admin"}, 1)

        self.assertIn("HTTP 503", raised.exception.detail)
        self.assertEqual(instance.url, "http://old-triton:8000")
        save.assert_not_called()
        service.collect_metrics_snapshot.assert_not_awaited()
        service.get_repository_index.assert_not_awaited()

    def test_GetInstanceByName_MissingRow_RaisesNotFound(self):
        # Act / Assert
        with patch("app.services.triton.instances.instances.find_by_name", return_value=None):
            with self.assertRaises(NotFoundError):
                triton_instances.get_instance_by_name(SimpleNamespace(), {}, "missing")


class TritonModelEdgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_GetModelConfig_Triton404_RaisesNotFound(self):
        # Arrange
        instance = SimpleNamespace(url="http://triton", triton_verify_ssl=False, triton_ca_certificate="")
        exc = Exception("missing")
        exc.response = SimpleNamespace(status_code=404)
        service = SimpleNamespace(get_model_config=AsyncMock(side_effect=exc))

        # Act / Assert
        with patch("app.services.triton.models.get_instance_or_404", return_value=instance), patch(
            "app.services.triton.models.TritonService",
            return_value=service,
        ):
            with self.assertRaises(NotFoundError):
                await triton_models.get_model_config(SimpleNamespace(), {}, 1, "m", "1")

    async def test_LoadAndUnloadModel_TritonError_UsesErrorDetail(self):
        # Arrange
        instance = SimpleNamespace(url="http://triton", triton_verify_ssl=False, triton_ca_certificate="")
        response = SimpleNamespace(json=lambda: {"error": "model broken"}, text="", status_code=400)
        exc = Exception("bad")
        exc.response = response
        service = SimpleNamespace(
            load_model=AsyncMock(side_effect=exc),
            unload_model=AsyncMock(side_effect=exc),
        )

        # Act / Assert
        with patch("app.services.triton.models.get_instance_or_404", return_value=instance), patch(
            "app.services.triton.models.TritonService",
            return_value=service,
        ):
            with self.assertRaises(BadRequestError) as load_error:
                await triton_models.load_model(SimpleNamespace(), {"role": "admin"}, 1, "m")
            with self.assertRaises(BadRequestError) as unload_error:
                await triton_models.unload_model(SimpleNamespace(), {"role": "admin"}, 1, "m")

        self.assertIn("model broken", str(load_error.exception))
        self.assertIn("model broken", str(unload_error.exception))

    async def test_InferModel_EmptyPayload_RaisesBadRequest(self):
        # Act / Assert
        with self.assertRaises(BadRequestError):
            await triton_models.infer_model(SimpleNamespace(), {}, 1, "m", "1", b"", "application/json")

    def test_EncodeMetricsHeader_ProducesUrlSafeJsonPayload(self):
        # Act
        encoded = triton_models._encode_metrics_header({"available": True, "models": []})
        decoded = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))

        # Assert
        self.assertEqual(decoded, {"available": True, "models": []})

    def test_WithMetricContext_UnavailableMetrics_AddsDiagnosticContext(self):
        # Act
        result = triton_models._with_metric_context(
            {"available": False, "error": "unchanged"},
            "metrics",
            {"series": {"m|1": {}}},
            {"series": {"m|1": {}, "m|2": {}}},
        )

        # Assert
        self.assertEqual(result["source"], "metrics")
        self.assertEqual(result["beforeSeriesCount"], 1)
        self.assertEqual(result["afterSeriesCount"], 2)
        self.assertIn("counters did not change", result["error"])


if __name__ == "__main__":
    unittest.main()
