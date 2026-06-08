"""Unit tests for ``InstanceHealthRefresher``.

Covers:
  - Full refresh cycle with all instances healthy.
  - S3 connectivity probing within the refresh cycle.
  - Alert generation when an instance is not live or not ready.
  - Database commit behaviour after each cycle.

All Triton HTTP calls and DB sessions are replaced with async/sync mocks.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from botocore.exceptions import SSLError
from sqlalchemy.orm.exc import StaleDataError

from app.db.entities import DashboardAlertEntity, TritonInstanceEntity
from app.services.storage.s3_client import (
    build_s3_client as _build_s3_client,
)
from app.services.storage.s3_client import (
    is_s3_configured as _can_probe_s3,
)
from app.services.triton.health import (
    DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS,
    DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS,
    InstanceHealthRefresher,
    _should_refresh_server_metadata,
    health_refresh_interval_seconds,
    health_request_timeout_seconds,
)


class _ExecResult:
    def __init__(self, all_rows=None):
        self._all = list(all_rows or [])

    def all(self):
        return self._all


class _FakeSession:
    def __init__(self, *, get_map=None, exec_rows=None):
        self.get_map = get_map or {}
        self.exec_rows = list(exec_rows or [])
        self.added = []
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def exec(self, _query):
        return _ExecResult(self.exec_rows.pop(0) if self.exec_rows else [])

    def get(self, _model, key):
        return self.get_map.get(key)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        return None


class InstanceHealthRefresherTests(unittest.IsolatedAsyncioTestCase):
    async def test_S3ProbeAndClientBuild_CompleteInstanceConfig_ReturnsConfiguredClient(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            s3_endpoint="http://minio",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
        )
        can_probe = _can_probe_s3(instance)

        # Act
        with patch("app.services.storage.s3_client.decrypt_secret", return_value="secret"), patch(
            "app.services.storage.s3_client.boto3.client", return_value="client"
        ) as boto:
            client = _build_s3_client(instance)

        # Assert
        self.assertTrue(can_probe)
        self.assertEqual(client, "client")
        self.assertEqual(boto.call_args.kwargs["aws_secret_access_key"], "secret")

    async def test_RefreshInstance_UnhealthySnapshotAndS3Failure_ReturnsExpectedAlerts(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            server_metadata=None,
            s3_endpoint="http://minio",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()

        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {"name": "srv"},
                    "live": False,
                    "ready": False,
                    "checked_at": datetime.now(timezone.utc),
                    "error": "down",
                }
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "m1", "state": "UNAVAILABLE"}]),
        )


        def _raise_s3(**_kwargs):
            raise SSLError(
                endpoint_url=instance.s3_endpoint,
                error="certificate verify failed: unable to get local issuer certificate",
            )

        bad_s3_client = SimpleNamespace(list_objects_v2=_raise_s3)

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ), patch("app.services.triton.health.build_s3_client", return_value=bad_s3_client), patch(
            "app.services.triton.health.logger"
        ) as logger:
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertGreaterEqual(len(alerts), 2)
        self.assertTrue(any(a.icon == "warning" for a in alerts))
        self.assertFalse(any(a.icon == "sync_problem" for a in alerts))
        self.assertTrue(any(a.icon == "cloud_off" for a in alerts))
        self.assertTrue(any("unable to get local issuer certificate" in a.label for a in alerts))
        logger.warning.assert_called_once()
        logger.exception.assert_not_called()

    async def test_RefreshInstance_InstanceDeletedDuringSave_ReturnsNoAlerts(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            server_metadata={},
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ), patch("app.services.triton.health.instances.save", side_effect=StaleDataError("deleted")):
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])

    async def test_RefreshInstance_SelfDeployedLoopbackUrl_DoesNotOverwriteConfiguredUrl(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://127.0.0.1:30270",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_service_name="gpu-a-service",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
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
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )
        service_urls = {
            "http": "http://192.168.49.2:30270",
            "metrics": "http://192.168.49.2:30876/metrics",
        }

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ) as triton_service, patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=["gpu-a: Running (Ready)"],
        ), patch(
            "app.services.deployment.kubernetes.is_pod_ready",
            return_value=True,
        ), patch(
            "app.services.deployment.kubernetes.resolve_deployment_service_urls",
            return_value=service_urls,
        ):
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        self.assertEqual(instance.url, "http://127.0.0.1:30270")
        self.assertIsNone(instance.metrics_url)
        triton_service.assert_called_with(
            "http://127.0.0.1:30270",
            False,
            "",
            timeout=DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS,
        )

    async def test_RefreshInstance_SelfDeployedPodNotReady_StoresWaitingState(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://127.0.0.1:30270",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_name="gpu-a",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService"
        ) as triton_service, patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=["gpu-a: Pending"],
        ), patch(
            "app.services.deployment.kubernetes.is_pod_ready",
            return_value=False,
        ), patch(
            "app.services.triton.health.instances.save"
        ) as save:
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        self.assertFalse(instance.health_live)
        self.assertFalse(instance.health_ready)
        self.assertEqual(instance.health_error, "Waiting for pod to become ready...")
        self.assertEqual(instance.pod_statuses, ["gpu-a: Pending"])
        save.assert_called_once_with(session, instance)
        triton_service.assert_not_called()

    async def test_RefreshInstance_SelfDeployedPodNotReadyAndDeleted_SwallowsStaleSave(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://127.0.0.1:30270",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_name="gpu-a",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=[],
        ), patch("app.services.deployment.kubernetes.is_pod_ready", return_value=False), patch(
            "app.services.triton.health.instances.save",
            side_effect=StaleDataError("deleted"),
        ):
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])

    async def test_RefreshInstance_SelfDeployedExternalUrl_DoesNotQueryKubernetes(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://192.168.49.2:30270",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_service_name="gpu-a-service",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[{"name": "m1", "version": "1", "state": "READY"}]),
        )

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ), patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=["gpu-a: Running (Ready)"],
        ), patch(
            "app.services.deployment.kubernetes.is_pod_ready",
            return_value=True,
        ), patch("app.services.deployment.kubernetes.resolve_deployment_service_urls") as resolve_urls:
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        resolve_urls.assert_not_called()
        self.assertEqual(instance.model_names, ["m1"])
        self.assertEqual(instance.repository_models, [{"name": "m1", "version": "1", "state": "READY"}])

    async def test_RefreshInstance_SelfDeployedReadyPod_StoresLatestPodStatuses(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://192.168.49.2:30270",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_name="gpu-a",
            deployment_service_name="gpu-a-service",
            pod_statuses=[],
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        latest_pod_statuses = ["gpu-a: Running (Ready)"]
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ), patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=latest_pod_statuses,
        ), patch(
            "app.services.deployment.kubernetes.is_pod_ready",
            return_value=True,
        ):
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        self.assertEqual(instance.pod_statuses, latest_pod_statuses)

    async def test_RefreshInstance_SelfDeployedClusterUrl_DoesNotOverwriteConfiguredUrl(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://gpu-a-service.gpu-a.svc.cluster.local:18000",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            is_self_deployed=True,
            deployment_namespace="gpu-a",
            deployment_service_name="gpu-a-service",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
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
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )
        service_urls = {
            "http": "http://192.168.49.2:18000",
            "metrics": "http://192.168.49.2:18002/metrics",
        }

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ) as triton_service, patch(
            "app.services.deployment.kubernetes.read_pod_statuses",
            return_value=["gpu-a: Running (Ready)"],
        ), patch(
            "app.services.deployment.kubernetes.is_pod_ready",
            return_value=True,
        ), patch(
            "app.services.deployment.kubernetes.resolve_deployment_service_urls",
            return_value=service_urls,
        ):
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        self.assertEqual(instance.url, "http://gpu-a-service.gpu-a.svc.cluster.local:18000")
        self.assertIsNone(instance.metrics_url)
        triton_service.assert_called_with(
            "http://gpu-a-service.gpu-a.svc.cluster.local:18000",
            False,
            "",
            timeout=DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS,
        )

    async def test_RefreshInstance_RepositoryIndexFailure_LogsAndKeepsHealthResult(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            server_metadata={},
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(side_effect=RuntimeError("repository unavailable")),
        )

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService",
            return_value=service,
        ), patch("app.services.triton.health.logger") as logger:
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(alerts, [])
        logger.exception.assert_called_once()

    async def test_RefreshInstance_S3UnexpectedFailure_ReturnsGenericS3Alert(self):
        # Arrange
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            server_metadata={},
            s3_endpoint="http://minio",
            s3_bucket="bucket",
            s3_access_key="ak",
            s3_secret_key_enc="enc",
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": None,
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )
        broken_s3_client = SimpleNamespace(list_objects_v2=lambda **_kwargs: (_ for _ in ()).throw(ValueError("boom")))

        # Act
        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService",
            return_value=service,
        ), patch("app.services.triton.health.build_s3_client", return_value=broken_s3_client), patch(
            "app.services.triton.health.logger"
        ) as logger:
            alerts = await refresher._refresh_instance(1)

        # Assert
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].icon, "cloud_off")
        self.assertEqual(alerts[0].label, "Configured S3 connection for gpu-a not reachable")
        logger.exception.assert_called_once()

    def test_ShouldRefreshServerMetadata_PlaceholderDeploymentStatus_ReturnsExpectedDecision(self):
        self.assertTrue(_should_refresh_server_metadata(None))
        self.assertTrue(
            _should_refresh_server_metadata({"name": "triton", "deployment_status": "deploying"})
        )
        self.assertFalse(_should_refresh_server_metadata({"name": "triton", "version": "2.51.0"}))

    def test_HealthRefreshIntervalSeconds_EnvValueOrInvalid_ReturnsExpectedInterval(self):
        with patch.dict("os.environ", {"TRITON_HEALTH_REFRESH_INTERVAL_SECONDS": "7"}, clear=False):
            self.assertEqual(health_refresh_interval_seconds(), 7)

        with patch.dict("os.environ", {"TRITON_HEALTH_REFRESH_INTERVAL_SECONDS": "0"}, clear=False), patch(
            "app.services.triton.health.logger"
        ) as logger:
            self.assertEqual(health_refresh_interval_seconds(), DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS)
            logger.warning.assert_called_once()

        with patch.dict("os.environ", {"TRITON_HEALTH_REFRESH_INTERVAL_SECONDS": "bad"}, clear=False), patch(
            "app.services.triton.health.logger"
        ) as logger:
            self.assertEqual(health_refresh_interval_seconds(), DEFAULT_HEALTH_REFRESH_INTERVAL_SECONDS)
            logger.warning.assert_called_once()

    def test_HealthRequestTimeoutSeconds_EnvValueOrInvalid_ReturnsExpectedTimeout(self):
        with patch.dict("os.environ", {"TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS": "2.5"}, clear=False):
            self.assertEqual(health_request_timeout_seconds(), 2.5)

        with patch.dict("os.environ", {"TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS": "0"}, clear=False), patch(
            "app.services.triton.health.logger"
        ) as logger:
            self.assertEqual(health_request_timeout_seconds(), DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS)
            logger.warning.assert_called_once()

        with patch.dict("os.environ", {"TRITON_HEALTH_REQUEST_TIMEOUT_SECONDS": "bad"}, clear=False), patch(
            "app.services.triton.health.logger"
        ) as logger:
            self.assertEqual(health_request_timeout_seconds(), DEFAULT_HEALTH_REQUEST_TIMEOUT_SECONDS)
            logger.warning.assert_called_once()

    async def test_RefreshInstance_DeployingPlaceholderMetadata_ReplacedByRuntimeMetadata(self):
        instance = TritonInstanceEntity(
            id=1,
            url="http://triton",
            name="gpu-a",
            model_names=[],
            server_metadata={"name": "triton", "deployment_status": "deploying"},
        )
        session = _FakeSession(get_map={1: instance})
        refresher = InstanceHealthRefresher()
        service = SimpleNamespace(
            collect_runtime_snapshot=AsyncMock(
                return_value={
                    "metadata": {"name": "triton", "version": "2.51.0"},
                    "live": True,
                    "ready": True,
                    "checked_at": datetime.now(timezone.utc),
                    "error": None,
                }
            ),
            get_repository_index=AsyncMock(return_value=[]),
        )

        with patch("app.services.triton.health.session_factory", return_value=session), patch(
            "app.services.triton.health.TritonService", return_value=service
        ):
            alerts = await refresher._refresh_instance(1)

        self.assertEqual(alerts, [])
        self.assertEqual(instance.server_metadata, {"name": "triton", "version": "2.51.0"})
        service.collect_runtime_snapshot.assert_awaited_once_with(include_metadata=True)

    async def test_RefreshAllInstances_AlertsProduced_PersistsDashboardAlerts(self):
        # Arrange
        refresher = InstanceHealthRefresher()
        read_session = _FakeSession(exec_rows=[[1, 2]])
        write_session = _FakeSession()

        # Act
        with patch(
            "app.services.triton.health.session_factory",
            side_effect=[read_session, write_session],
        ), patch.object(
            refresher,
            "_refresh_instance",
            AsyncMock(
                side_effect=[
                    [DashboardAlertEntity(icon="x", label="a", tone="warn")],
                    [],
                ]
            ),
        ):
            await refresher._refresh_all_instances()

        # Assert
        self.assertEqual(write_session.commit_count, 1)
        self.assertEqual(len(write_session.added), 1)

    async def test_StartStopRunLoop_TaskLifecycleTransitions_HandledCorrectly(self):
        # Arrange
        refresher = InstanceHealthRefresher(interval_seconds=0.01)

        # Act
        with patch.object(refresher, "_run", AsyncMock(return_value=None)):
            refresher.start()
            self.assertIsNotNone(refresher._task)
            await refresher.stop()
        first_stop_task = refresher._task

        # Arrange
        refresher = InstanceHealthRefresher(interval_seconds=0.01)

        # Act
        with patch.object(refresher, "_refresh_all_instances", AsyncMock(side_effect=[RuntimeError("boom"), None])):
            task = asyncio.create_task(refresher._run())
            await asyncio.sleep(0.03)
            refresher._stop_event.set()
            await task

        # Assert
        self.assertIsNone(first_stop_task)

    async def test_Stop_RunLoopInFlight_CancelsWithoutRaising(self):
        # Arrange
        started = asyncio.Event()
        refresher = InstanceHealthRefresher(interval_seconds=60)

        async def _blocked_refresh():
            started.set()
            await asyncio.Event().wait()

        # Act
        with patch.object(refresher, "_refresh_all_instances", _blocked_refresh):
            refresher.start()
            await started.wait()
            await refresher.stop()

        # Assert
        self.assertIsNone(refresher._task)


if __name__ == "__main__":
    unittest.main()
