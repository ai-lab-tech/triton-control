import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.exceptions import NotFoundError
from app.schemas import UpdateUserInstancesRequest, UpdateUserRoleRequest
from app.services import users as users_service
from app.services.deployment import kubernetes as deployment_k8s
from app.services.triton import config as triton_config
from app.services.triton import health as triton_health
from app.services.triton import instances as triton_instances
from app.services.triton import models as triton_models


class UsersServiceTests(unittest.TestCase):
    def test_GetUserOr404_UserMissing_RaisesNotFound(self):
        with patch("app.services.users.users.find_by_id", return_value=None):
            with self.assertRaises(NotFoundError):
                users_service.get_user_or_404(SimpleNamespace(), 42)

    def test_UpdateUserRole_ValidPayload_PersistsAndReturnsDto(self):
        session = SimpleNamespace()
        row = SimpleNamespace(id=7, role="viewer", is_active=False)
        dto = {"id": 7, "role": "admin"}
        request = UpdateUserRoleRequest(role="admin")

        with patch("app.services.users.require_admin"), patch(
            "app.services.users.get_user_or_404",
            return_value=row,
        ), patch("app.services.users.users.save") as save_mock, patch(
            "app.services.users.user_entity_to_dto",
            return_value=dto,
        ):
            result = users_service.update_user_role(session, {"role": "admin"}, 7, request)

        self.assertEqual(result, dto)
        self.assertEqual(row.role, "admin")
        self.assertTrue(row.is_active)
        save_mock.assert_called_once_with(session, row)

    def test_UpdateUserInstances_ValidPayload_PersistsAndReturnsDto(self):
        session = SimpleNamespace()
        row = SimpleNamespace(id=9, assigned_instances=[])
        dto = {"id": 9, "assigned_instances": ["node-a"]}
        request = UpdateUserInstancesRequest(assigned_instances=["node-a"])

        with patch("app.services.users.require_admin"), patch(
            "app.services.users.get_user_or_404",
            return_value=row,
        ), patch("app.services.users.users.save") as save_mock, patch(
            "app.services.users.user_entity_to_dto",
            return_value=dto,
        ):
            result = users_service.update_user_instances(session, {"role": "admin"}, 9, request)

        self.assertEqual(result, dto)
        self.assertEqual(row.assigned_instances, ["node-a"])
        save_mock.assert_called_once_with(session, row)


class TritonConfigHelperTests(unittest.TestCase):
    def test_MapVersionToReleaseBranch_HandlesBranchStringAndSemver(self):
        self.assertEqual(triton_config._map_triton_version_to_release_branch("25.02"), "r25.02")
        self.assertEqual(triton_config._map_triton_version_to_release_branch("triton 2.55.0"), "r25.02")

    def test_MapVersionToReleaseBranch_InvalidInput_ReturnsNone(self):
        self.assertIsNone(triton_config._map_triton_version_to_release_branch(""))
        self.assertIsNone(triton_config._map_triton_version_to_release_branch("not-a-version"))

    def test_LoadModelConfigClass_CacheHit_ReturnsCachedClass(self):
        class FakeModelConfig:
            pass

        triton_config._MODEL_CONFIG_CLASS_CACHE["r-cache"] = FakeModelConfig
        try:
            resolved = triton_config._load_model_config_class("r-cache")
        finally:
            triton_config._MODEL_CONFIG_CLASS_CACHE.pop("r-cache", None)
        self.assertIs(resolved, FakeModelConfig)

    def test_ExtractTritonErrorDetail_ResponseShapes_ReturnsBestMessage(self):
        exc_no_response = Exception("x")
        self.assertEqual(triton_config.extract_triton_error_detail(exc_no_response), "Triton request failed")

        exc_detail = Exception("x")
        exc_detail.response = SimpleNamespace(json=lambda: {"detail": "bad detail"}, text="", status_code=400)
        self.assertEqual(triton_config.extract_triton_error_detail(exc_detail), "bad detail")

        exc_text = Exception("x")
        exc_text.response = SimpleNamespace(
            json=lambda: (_ for _ in ()).throw(ValueError("bad json")),
            text="plain error",
            status_code=503,
        )
        self.assertEqual(triton_config.extract_triton_error_detail(exc_text), "plain error")


class TritonHelpersTests(unittest.TestCase):
    def test_ShouldRefreshServerMetadata_BranchesMatchExpected(self):
        self.assertTrue(triton_health._should_refresh_server_metadata(None))
        self.assertFalse(triton_health._should_refresh_server_metadata("bad-shape"))
        self.assertTrue(triton_health._should_refresh_server_metadata({"deployment_status": "deploying"}))
        self.assertFalse(triton_health._should_refresh_server_metadata({"deployment_status": "running"}))

    def test_FormatTritonUnavailableDetail_WithAndWithoutError(self):
        self.assertEqual(
            triton_instances._format_triton_unavailable_detail("http://t", " down "),
            "Triton server at http://t is not ready: down",
        )
        self.assertEqual(
            triton_instances._format_triton_unavailable_detail("http://t", None),
            "Triton server at http://t is not ready",
        )

    def test_SeriesCount_SnapshotShapes_ReturnsExpectedCount(self):
        self.assertEqual(triton_models._series_count(None), 0)
        self.assertEqual(triton_models._series_count({"series": []}), 0)
        self.assertEqual(triton_models._series_count({"series": {"a": {}, "b": {}}}), 2)

    def test_IsMetricsSnapshotAvailable_BranchesMatchExpected(self):
        self.assertFalse(triton_models._is_metrics_snapshot_available(None))
        self.assertFalse(triton_models._is_metrics_snapshot_available({"error": "failed"}))
        self.assertTrue(triton_models._is_metrics_snapshot_available({"error": ""}))

    def test_WithMetricContext_AvailableMetrics_PreservesPayloadAndSetsSource(self):
        metrics = {"available": True, "models": [{"name": "m"}]}
        result = triton_models._with_metric_context(metrics, "stats", {"series": {}}, {"series": {}})
        self.assertTrue(result["available"])
        self.assertEqual(result["source"], "stats")
        self.assertEqual(result["models"][0]["name"], "m")

    def test_WithMetricContext_NoSeries_AddsNoRowsDiagnostic(self):
        result = triton_models._with_metric_context(
            {"available": False},
            "stats",
            {"series": {}},
            {"series": {}},
        )
        self.assertFalse(result["available"])
        self.assertIn("did not expose inference metric rows", result["error"])
        self.assertEqual(result["beforeSeriesCount"], 0)
        self.assertEqual(result["afterSeriesCount"], 0)

    def test_ExtractTritonVersion_ValidAndTrimmed_ReturnsVersion(self):
        self.assertEqual(
            triton_config.extract_triton_version({"version": " 2.55.0 "}),
            "2.55.0",
        )


class DeploymentKubernetesHelperTests(unittest.TestCase):
    def test_PendingUrls_WithAndWithoutIngressHost_ReturnExpectedUrls(self):
        self.assertEqual(
            deployment_k8s.pending_url("ns", "svc"),
            "http://svc.ns.svc.cluster.local:18000",
        )
        self.assertEqual(
            deployment_k8s.pending_metrics_url("ns", "svc"),
            "http://svc.ns.svc.cluster.local:18002/metrics",
        )
        self.assertEqual(
            deployment_k8s.pending_url("ns", "svc", "example.com"),
            "https://example.com",
        )
        self.assertEqual(
            deployment_k8s.pending_metrics_url("ns", "svc", "https://example.com/"),
            "https://example.com/metrics",
        )

    def test_ApiError_FormatsDetailsAcrossPayloadShapes(self):
        exc = SimpleNamespace(reason="forbidden", body="no access", status=403)
        self.assertEqual(
            deployment_k8s._api_error(exc),
            "Kubernetes API error 403: forbidden - no access",
        )

        exc_reason_only = SimpleNamespace(reason="boom", body="", status=None)
        self.assertEqual(deployment_k8s._api_error(exc_reason_only), "boom")


if __name__ == "__main__":
    unittest.main()
