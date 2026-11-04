"""Exercise MLflow startup under the workspace's inherited Python path."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.schemas.development import CreateCodeServerRequest
from app.services.development import kubernetes as k8s
from app.services.development.mlflow_setup import setup_command


class MlflowSetupTests(unittest.TestCase):
    def setUp(self):
        self.config = patch.dict(
            os.environ,
            {
                "DEVELOPMENT_MLFLOW_VERSION": "",
                "DEVELOPMENT_MLFLOW_TRITON_CONTROL_VERSION": "",
            },
        )
        self.config.start()
        self.addCleanup(self.config.stop)
        temporary = tempfile.TemporaryDirectory(prefix="mlflow startup ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        binary = self.root / "python3"
        binary.write_text(
            "#!/bin/sh\n"
            'printf "%s|%s\\n" "$*" "$PYTHONPATH" >> "$TEST_LOG"\n'
            'if [ "$1" = "-c" ]; then\n'
            '  case "$2" in\n'
            '    *getusersitepackages*) printf "%s" "$TEST_SITE";;\n'
            '    *) test -f "$TEST_READY";;\n'
            "  esac\n"
            'elif [ "$2" = "pip" ]; then\n'
            '  [ "$TEST_FAIL" = pip ] && exit 1\n'
            '  touch "$TEST_READY"\n'
            'elif [ "$2" = "ipykernel" ]; then\n'
            '  [ "$TEST_FAIL" = kernel ] && exit 1\n'
            "  exit 0\n"
            "else exit 1; fi\n"
        )
        binary.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": f"{self.root}:{os.environ['PATH']}",
            "PYTHONPATH": "/opt/tritonserver/backends/dali/wheel/dali",
            "TEST_SITE": str(self.root / "user site"),
            "TEST_LOG": str(self.root / "log"),
            "TEST_READY": str(self.root / "ready"),
            "TEST_FAIL": "",
        }

    def run_setup(self, request=None):
        return subprocess.run(
            ["sh", "-c", setup_command(request or CreateCodeServerRequest()) + "echo editor-started"],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_installs_once_in_shared_python_and_prioritizes_user_site(self):
        # Arrange
        request = CreateCodeServerRequest()
        tested_plugin = Path(k8s.__file__).with_name("mlflow-triton-control-version.txt").read_text().strip()
        expected_path = self.environment["TEST_SITE"] + ":" + self.environment["PYTHONPATH"]

        # Act
        results = [self.run_setup(request) for _ in range(2)]
        calls = (self.root / "log").read_text().splitlines()
        installs = [line for line in calls if "-m pip install" in line]
        kernels = [line for line in calls if "-m ipykernel" in line]

        # Assert
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("editor-started", result.stdout)
        self.assertEqual(len(installs), 1)
        self.assertIn("--user --ignore-installed", installs[0])
        self.assertIn("mlflow==3.14.0", installs[0])
        self.assertIn(f"mlflow-triton-control=={tested_plugin}", installs[0])
        self.assertIn("numpy<2", installs[0])
        self.assertTrue(installs[0].endswith("|" + expected_path))
        self.assertEqual(len(kernels), 2)
        self.assertIn("--name triton-control", kernels[0])
        self.assertIn("--env PYTHONPATH " + expected_path, kernels[0])

    def test_failures_stop_before_starting_editor(self):
        for failure in ("pip", "kernel"):
            with self.subTest(failure=failure):
                # Arrange
                self.environment["TEST_FAIL"] = failure
                Path(self.environment["TEST_READY"]).unlink(missing_ok=True)

                # Act
                result = self.run_setup()

                # Assert
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("editor-started", result.stdout)
                self.assertIn("Error:", result.stderr)

    def test_environment_versions_override_tested_defaults(self):
        # Arrange
        request = CreateCodeServerRequest()
        configuration = {
            "DEVELOPMENT_MLFLOW_VERSION": "3.15.0",
            "DEVELOPMENT_MLFLOW_TRITON_CONTROL_VERSION": "0.5.0",
        }

        # Act
        with patch.dict(os.environ, configuration):
            command = setup_command(request)

        # Assert
        self.assertIn("mlflow==3.15.0", command)
        self.assertIn("mlflow-triton-control==0.5.0", command)

    def test_request_versions_override_environment_versions(self):
        # Arrange
        request = CreateCodeServerRequest(mlflow_version=" 3.14.0 ", mlflow_triton_control_version="0.4.2")
        configuration = {
            "DEVELOPMENT_MLFLOW_VERSION": "3.15.0",
            "DEVELOPMENT_MLFLOW_TRITON_CONTROL_VERSION": "0.5.0",
        }

        # Act
        with patch.dict(os.environ, configuration):
            command = setup_command(request)

        # Assert
        self.assertIn("mlflow==3.14.0", command)
        self.assertIn("mlflow-triton-control==0.4.2", command)

    def test_invalid_package_versions_are_rejected(self):
        for field in ("mlflow_version", "mlflow_triton_control_version"):
            with self.subTest(field=field):
                # Arrange
                values = {field: "1.0; echo injected"}

                # Act / Assert
                with self.assertRaises(ValidationError):
                    CreateCodeServerRequest(**values)

    def test_disabled_option_skips_setup_and_enabled_option_shares_interpreter(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                # Arrange
                request = CreateCodeServerRequest(install_hf_builder=False, install_mlflow=enabled)

                # Act
                manifest = k8s._statefulset_manifest(request, "dev", "workspace", "service", {})
                command = manifest["spec"]["template"]["spec"]["containers"][0]["args"][0]

                # Assert
                self.assertEqual("mlflow-triton-control==" in command, enabled)
                self.assertEqual('"python.defaultInterpreterPath":"python3"' in command, enabled)
                self.assertEqual("--install-extension ms-toolsai.jupyter" in command, enabled)
                self.assertNotIn("virtualenv", command)
                if enabled:
                    self.assertLess(command.index("mlflow-triton-control=="), command.index('exec "$CODE_SERVER_BIN"'))
