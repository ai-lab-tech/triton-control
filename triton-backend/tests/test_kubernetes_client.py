"""Unit tests for backend Kubernetes API client configuration."""

import unittest
from unittest.mock import patch

from app.services.kubernetes_client import api_client


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
