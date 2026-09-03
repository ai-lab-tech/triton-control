"""Unit tests for backend Kubernetes API client configuration."""

import unittest
from unittest.mock import Mock, patch

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.services.kubernetes_client import api_client, gpu_runtime_class_name


class KubernetesClientTests(unittest.TestCase):
    def test_ApiClient_KubeconfigPathConfigured_LoadsPath(self) -> None:
        with patch.dict("os.environ", {"KUBERNETES_KUBECONFIG_PATH": "C:/kube/config"}), patch(
            "kubernetes.config.kube_config.load_kube_config"
        ) as load_kube_config, patch(
            "kubernetes.config.incluster_config.load_incluster_config"
        ) as load_incluster_config, patch("kubernetes.client.ApiClient", return_value="api"):
            client = api_client()

        self.assertEqual(client, "api")
        load_kube_config.assert_called_once_with(config_file="C:/kube/config")
        load_incluster_config.assert_not_called()

    def test_ApiClient_KubeconfigPathEmpty_LoadsInClusterConfig(self) -> None:
        with patch.dict("os.environ", {"KUBERNETES_KUBECONFIG_PATH": ""}), patch(
            "kubernetes.config.kube_config.load_kube_config"
        ) as load_kube_config, patch(
            "kubernetes.config.incluster_config.load_incluster_config"
        ) as load_incluster_config, patch("kubernetes.client.ApiClient", return_value="api"):
            client = api_client()

        self.assertEqual(client, "api")
        load_kube_config.assert_not_called()
        load_incluster_config.assert_called_once_with()

    def test_GpuRuntimeClassName_GpuAndRuntimeClassExists_ReturnsName(self) -> None:
        node_api = Mock()

        with patch("kubernetes.client.NodeV1Api", return_value=node_api):
            result = gpu_runtime_class_name("api", 1)

        self.assertEqual(result, "nvidia")
        node_api.read_runtime_class.assert_called_once_with(name="nvidia")

    def test_GpuRuntimeClassName_NoGpu_DoesNotInspectCluster(self) -> None:
        with patch("kubernetes.client.NodeV1Api") as node_api:
            result = gpu_runtime_class_name("api", 0)

        self.assertIsNone(result)
        node_api.assert_not_called()

    def test_GpuRuntimeClassName_RuntimeClassMissing_ReturnsNone(self) -> None:
        node_api = Mock()
        node_api.read_runtime_class.side_effect = ApiException(status=404, reason="Not Found")

        with patch("kubernetes.client.NodeV1Api", return_value=node_api):
            result = gpu_runtime_class_name("api", 1)

        self.assertIsNone(result)

    def test_GpuRuntimeClassName_ConfiguredName_UsesOverride(self) -> None:
        node_api = Mock()

        with patch.dict("os.environ", {"NVIDIA_RUNTIME_CLASS_NAME": "gpu-runtime"}), patch(
            "kubernetes.client.NodeV1Api",
            return_value=node_api,
        ):
            result = gpu_runtime_class_name("api", 2)

        self.assertEqual(result, "gpu-runtime")
        node_api.read_runtime_class.assert_called_once_with(name="gpu-runtime")
