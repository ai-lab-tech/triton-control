"""Rendered chart separates backend submission rights from workflow execution."""

import shutil
import subprocess
import unittest
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

CHART = Path(__file__).resolve().parents[2] / "charts" / "triton-control"


@unittest.skipUnless(shutil.which("helm"), "Helm is required for chart checks")
class WorkflowsChartTests(unittest.TestCase):
    @classmethod
    def render(cls, *overrides: str) -> list[dict[str, Any]]:
        command = [
            "helm", "template", "security-test", str(CHART), "--namespace", "triton-control",
            "--set", "argoWorkflows.enabled=true", "--set", "argoWorkflows.crds.install=false",
        ]
        for value in overrides:
            command.extend(["--set", value])
        output = subprocess.check_output(command, text=True)  # nosec B603 - fixed Helm command and test inputs
        return [item for item in yaml.safe_load_all(output) if item]

    def test_client_auth_and_network_selectors_match_real_pods(self):
        # Arrange: render() supplies the default embedded Argo configuration.

        # Act
        resources = self.render()

        # Assert
        server = next(item for item in resources
                      if item["kind"] == "Deployment" and item["metadata"]["name"] == "argo-workflows-server")
        args = server["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--auth-mode=client", args)
        self.assertNotIn("--auth-mode=server", args)
        policy = next(item for item in resources if item["kind"] == "NetworkPolicy")
        for key, value in policy["spec"]["podSelector"]["matchLabels"].items():
            self.assertEqual(server["spec"]["template"]["metadata"]["labels"][key], value)
        peers = policy["spec"]["ingress"][0]["from"]
        self.assertEqual(len(peers), 1)
        app = next(item for item in resources
                   if item["kind"] == "Deployment" and item["metadata"]["name"] == "security-test-triton-control")
        for key, value in peers[0]["podSelector"]["matchLabels"].items():
            self.assertEqual(app["spec"]["template"]["metadata"]["labels"][key], value)
        self.assertEqual(policy["spec"]["ingress"][0]["ports"], [{"protocol": "TCP", "port": 2746}])

    def test_backend_binding_and_executor_permissions_are_separate(self):
        # Arrange: render() supplies the default backend and executor accounts.

        # Act
        resources = self.render()

        # Assert
        binding = next(item for item in resources
                       if item["kind"] == "RoleBinding" and item["metadata"]["name"].endswith("-argo-proxy"))
        self.assertEqual(binding["subjects"][0]["name"], "security-test-triton-control")
        executor_role = next(item for item in resources
                             if item["kind"] == "Role" and item["metadata"]["name"] == "argo-workflows-workflow")
        self.assertEqual(executor_role["rules"], [{
            "apiGroups": ["argoproj.io"], "resources": ["workflowtaskresults"], "verbs": ["create", "patch"],
        }])
        backend_role = next(item for item in resources
                            if item["kind"] == "Role" and item["metadata"]["name"].endswith("-argo-proxy"))
        workflow_rule = next(rule for rule in backend_role["rules"] if "workflows" in rule["resources"])
        self.assertIn("create", workflow_rule["verbs"])

    def test_custom_serviceaccount_is_bound_and_network_policy_can_be_disabled(self):
        # Arrange
        overrides = ("serviceAccount.name=custom-backend", "argoIntegration.networkPolicy.enabled=false")

        # Act
        resources = self.render(*overrides)

        # Assert
        binding = next(item for item in resources
                       if item["kind"] == "RoleBinding" and item["metadata"]["name"].endswith("-argo-proxy"))
        self.assertEqual(binding["subjects"][0]["name"], "custom-backend")
        self.assertFalse(any(item["kind"] == "NetworkPolicy" for item in resources))

    def test_insecure_server_mode_override_is_rejected(self):
        # Arrange
        override = "argoWorkflows.server.authModes[0]=server"

        # Act
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.render(override)

        # Assert
        self.assertNotEqual(raised.exception.returncode, 0)

    def test_disabled_argo_does_not_add_submission_permissions(self):
        # Arrange
        override = "argoWorkflows.enabled=false"

        # Act
        resources = self.render(override)

        # Assert
        self.assertFalse(any(item["metadata"]["name"].endswith("-argo-proxy") for item in resources))
