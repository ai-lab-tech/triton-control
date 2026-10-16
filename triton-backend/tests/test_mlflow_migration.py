"""Automatic MLflow migration uses existing non-destructive upgrade operations."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.mlflow import kubernetes as k8s
from app.services.mlflow import migration


class MigrationTests(unittest.TestCase):
    def test_legacy_installation_is_upgraded_automatically(self):
        # Arrange
        entity = SimpleNamespace(namespace="control", deployment_name="mlflow", status="ready")
        with patch.object(migration, "session_factory"), patch.object(migration.mlflow, "get", return_value=entity), \
                patch.object(k8s, "tracking_upgrade_required", return_value=True), \
                patch.object(migration.installer, "upgrade_mlflow_tracking") as upgrade:
            # Act
            migration.reconcile()
            # Assert
            upgrade.assert_called_once()

    def test_current_installation_is_left_unchanged(self):
        # Arrange
        entity = SimpleNamespace(namespace="control", deployment_name="mlflow", status="ready")
        with patch.object(migration, "session_factory"), patch.object(migration.mlflow, "get", return_value=entity), \
                patch.object(k8s, "tracking_upgrade_required", return_value=False), \
                patch.object(migration.installer, "upgrade_mlflow_tracking") as upgrade:
            # Act
            migration.reconcile()
            # Assert
            upgrade.assert_not_called()

    def test_interrupted_upgrade_is_retried_even_with_gateway_present(self):
        # Arrange
        entity = SimpleNamespace(namespace="control", deployment_name="mlflow", status="upgrading")
        with patch.object(migration, "session_factory"), patch.object(migration.mlflow, "get", return_value=entity), \
                patch.object(k8s, "tracking_upgrade_required", return_value=False), \
                patch.object(migration.installer, "upgrade_mlflow_tracking") as upgrade:
            # Act
            migration.reconcile()
            # Assert
            upgrade.assert_called_once()

    def test_absent_installation_does_not_create_resources(self):
        # Arrange
        with patch.object(migration, "session_factory"), patch.object(migration.mlflow, "get", return_value=None), \
                patch.object(k8s, "tracking_upgrade_required") as detect:
            # Act
            migration.reconcile()
            # Assert
            detect.assert_not_called()


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_migration_is_retried_without_stopping_worker(self):
        # Arrange
        with patch.object(migration.asyncio, "to_thread", AsyncMock(side_effect=[RuntimeError("unavailable"), None])) \
                as reconcile, patch.object(migration.asyncio, "sleep", AsyncMock(
                    side_effect=[None, asyncio.CancelledError()],
                )) as sleep, patch.object(migration.logger, "exception") as log:
            # Act
            with self.assertRaises(asyncio.CancelledError):
                await migration._run()
            # Assert
            self.assertEqual(reconcile.await_count, 2)
            self.assertEqual(sleep.await_count, 2)
            sleep.assert_awaited_with(60)
            log.assert_called_once()


class DetectionTests(unittest.TestCase):
    def test_legacy_deployment_without_sidecar_needs_upgrade(self):
        # Arrange
        deployment = SimpleNamespace(spec=SimpleNamespace(template=SimpleNamespace(
            spec=SimpleNamespace(containers=[SimpleNamespace(name="mlflow")]),
        )))
        with patch.object(k8s, "_client"), patch("kubernetes.client.AppsV1Api") as apps:
            apps.return_value.read_namespaced_deployment.return_value = deployment
            # Act
            required = k8s.tracking_upgrade_required("control", "mlflow")
            # Assert
            self.assertTrue(required)

    def test_gateway_secret_and_sidecar_make_detection_idempotent(self):
        # Arrange
        deployment = SimpleNamespace(spec=SimpleNamespace(template=SimpleNamespace(
            spec=SimpleNamespace(containers=[SimpleNamespace(name="tracking-gateway")]),
        )))
        with patch.object(k8s, "_client"), patch("kubernetes.client.AppsV1Api") as apps, \
                patch("kubernetes.client.CoreV1Api") as core:
            apps.return_value.read_namespaced_deployment.return_value = deployment
            core.return_value.read_namespaced_secret.return_value = SimpleNamespace(data={"gateway-token": "encoded"})
            # Act
            required = k8s.tracking_upgrade_required("control", "mlflow")
            # Assert
            self.assertFalse(required)
