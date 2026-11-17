"""Render real manifests to verify the production deployment boundary."""

import shutil
import subprocess
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]

CHART = Path(__file__).resolve().parents[2] / "charts/triton-control"


@unittest.skipUnless(shutil.which("helm"), "Helm required for chart rendering")
class InstallationHelmTests(unittest.TestCase):
    def render(self, *args):
        return subprocess.run(
            ["helm", "template", "policy-test", str(CHART), *args],
            capture_output=True, text=True, check=False,
        )

    def test_production_omits_development_configuration(self):
        # Arrange / Act
        result = self.render("-f", str(CHART / "values-production.yaml"))
        # Assert
        self.assertEqual(result.returncode, 0, result.stderr)
        documents = list(yaml.safe_load_all(result.stdout))
        deployment = next(doc for doc in documents if doc and doc.get("kind") == "Deployment")
        env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertIn({"name": "TRITON_CONTROL_MODE", "value": "production"}, env)
        self.assertFalse(any(item["name"].startswith(("MLFLOW_", "DEVELOPMENT_", "ARGO_WORKFLOWS_")) for item in env))

        roles = [doc for doc in documents if doc and doc.get("kind") in {"Role", "ClusterRole"}]
        self.assertTrue(roles)
        for role in roles:
            for rule in role["rules"]:
                self.assertLessEqual(set(rule["verbs"]), {"get", "list", "watch"})
                self.assertNotIn("pods/exec", rule["resources"])

    def test_invalid_mode_and_production_argo_are_rejected(self):
        # Arrange
        configurations = [
            ["--set", "app.env[0].name=TRITON_CONTROL_MODE", "--set", "app.env[0].value=invalid"],
            ["-f", str(CHART / "values-production.yaml"), "--set", "argoWorkflows.enabled=true"],
        ]
        for arguments in configurations:
            with self.subTest(arguments=arguments):
                # Act / Assert
                self.assertNotEqual(self.render(*arguments).returncode, 0)
