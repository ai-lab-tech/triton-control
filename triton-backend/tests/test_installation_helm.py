"""Render real manifests to verify the production deployment boundary."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml  # type: ignore[import-untyped]

CHART = Path(__file__).resolve().parents[2] / "charts/triton-control"


@unittest.skipUnless(shutil.which("helm"), "Helm required for chart rendering")
class InstallationHelmTests(unittest.TestCase):
    def render(self, *args, release="policy-test", external_secret=True):
        secret = ["--set", "app.existingSecret=app-fixture"] if external_secret else []
        return subprocess.run(
            ["helm", "template", release, str(CHART), *secret, *args],
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

    def test_production_mode_survives_an_environment_overlay(self):
        result = self.render(
            "-f", str(CHART / "values-production.yaml"),
            "--set", "app.env[0].name=BACKEND_VERBOSE", "--set-string", "app.env[0].value=true",
            "--show-only", "templates/deployment.yaml",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        env = yaml.safe_load(result.stdout)["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual([item["value"] for item in env if item["name"] == "TRITON_CONTROL_MODE"], ["production"])

    def test_app_secret_values_are_stable_and_required(self):
        missing = self.render(external_secret=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Set a stable app.secretEnv.", missing.stderr)
        arguments = [
            "--show-only", "templates/secret.yaml", "--set-string",
            "app.secretEnv.SESSION_SECRET=fixture-session,app.secretEnv.JWT_SECRET=fixture-jwt,"
            "app.secretEnv.S3_SECRET_ENCRYPTION_KEY=fixture-storage",
        ]
        first = self.render(*arguments, external_secret=False)
        second = self.render(*arguments, external_secret=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(len(yaml.safe_load(first.stdout)["stringData"]), 3)

    def test_first_install_generates_s3_key_and_upgrade_requires_original(self):
        args = ["--show-only", "templates/secret.yaml", "--set-string",
                "app.secretEnv.JWT_SECRET=fixture-jwt,app.secretEnv.SESSION_SECRET=fixture-session"]
        def install():
            result = self.render(*args, external_secret=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            return yaml.safe_load(result.stdout)["stringData"]["S3_SECRET_ENCRYPTION_KEY"]

        first = install()
        self.assertRegex(first, r"^[A-Za-z0-9]{64}$")
        self.assertNotEqual(first, install())
        missing = self.render(*args, "--is-upgrade", external_secret=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Restore S3_SECRET_ENCRYPTION_KEY", missing.stderr)

    def test_development_mounts_snapshot_claim_unless_an_external_store_is_configured(self):
        for options, expected in [
            ([], True),
            (["--set", "app.env[0].name=TRITON_PREPARATION_SNAPSHOT_PROFILE_ID",
              "--set-string", "app.env[0].value=2"], False),
            (["-f", str(CHART / "values-production.yaml")], False),
        ]:
            with self.subTest(options=options):
                result = self.render(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("TRITON_PREPARATION_SNAPSHOT_DEFAULT_ROOT" in result.stdout, expected)
                self.assertEqual("claimName: policy-test-triton-control-snapshots" in result.stdout, expected)

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

    def test_release_sources_file_mount_is_read_only_and_changes_rollout(self):
        # Arrange
        fixture = CHART.parents[1] / "contracts/serving/v1alpha1/fixtures/source-s3.yaml"
        source = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / "values.yaml"

            def render():
                path.write_text(yaml.safe_dump({"releaseSources": {"file": [source]}}), encoding="utf-8")
                result = self.render("-f", str(path))
                self.assertEqual(result.returncode, 0, result.stderr)
                return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

            # Act
            before = render()
            source["spec"]["enabled"] = True
            after = render()
            conflict = self.render("-f", str(path), "--set", "app.env[0].name=TRITON_RELEASE_SOURCES_JSON")

        # Assert
        config = next(doc for doc in before if doc["kind"] == "ConfigMap" and "sources.json" in doc.get("data", {}))
        updated = next(doc for doc in after if doc["kind"] == "ConfigMap" and "sources.json" in doc.get("data", {}))
        deployment = next(doc for doc in before if doc["kind"] == "Deployment")["spec"]["template"]["spec"]
        container = deployment["containers"][0]
        self.assertIn({"name": "TRITON_RELEASE_SOURCES_FILE", "value": "/etc/triton-control/release-sources/sources.json"},
                      container["env"])
        self.assertTrue(next(mount for mount in container["volumeMounts"] if mount["name"] == "release-sources")["readOnly"])
        self.assertEqual(next(volume for volume in deployment["volumes"] if volume["name"] == "release-sources")
                         ["configMap"]["name"], config["metadata"]["name"])
        self.assertNotEqual(config["metadata"]["name"], updated["metadata"]["name"])
        self.assertFalse(json.loads(config["data"]["sources.json"])[0]["spec"].get("enabled", False))
        self.assertIn("Choose one release source input", conflict.stderr)

    def test_values_before_release_sources_render_without_config_input(self):
        # Arrange / Act
        result = self.render("--set", "releaseSources=null")

        # Assert
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("TRITON_RELEASE_SOURCES_", result.stdout)

    def test_source_rollout_excludes_old_pods_even_when_removing_last_source(self):
        for args in ([], ["--set-json", 'releaseSources.file=[{"kind":"MetadataSource"}]']):
            with self.subTest(args=args):
                result = self.render("--show-only", "templates/deployment.yaml", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(yaml.safe_load(result.stdout)["spec"]["strategy"], {"type": "Recreate"})

    def test_long_release_names_get_distinct_source_config_maps(self):
        names = []
        for region in ("east", "west"):
            result = self.render(
                "--show-only", "templates/release-sources.yaml", "--set-json",
                'releaseSources.file=[{"kind":"MetadataSource"}]',
                release=f"triton-control-release-registration-environment-{region}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            names.append(yaml.safe_load(result.stdout)["metadata"]["name"])
        self.assertNotEqual(*names)
