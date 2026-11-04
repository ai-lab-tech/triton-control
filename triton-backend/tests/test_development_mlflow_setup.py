"""Exercise MLflow startup under the workspace's inherited Python path."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from app.schemas.development import CreateCodeServerRequest
from app.services.development import kubernetes as k8s
from app.services.development.mlflow_setup import setup_command
from app.services.development.python_packages import NUMPY_REPAIR_SCRIPT, NUMPY_REQUIREMENT_SCRIPT, USER_INSTALL_FLAGS


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
            '    *numpy-repair-*) exit 0;;\n'
            '    *getusersitepackages*) printf "%s" "$TEST_SITE";;\n'
            '    *"requires(\x27tritonserver\x27)"*) printf "%s" "$TEST_NUMPY_REQUIREMENT";;\n'
            "    *numpy-repair-*) exit 0;;\n"
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
            "TEST_NUMPY_REQUIREMENT": "numpy<2",
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
        self.assertIn("--user --upgrade --force-reinstall", installs[0])
        self.assertNotIn("--ignore-installed", installs[0])
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

    def test_numpy_constraint_comes_from_image_metadata_and_respects_markers(self):
        for dependencies, expected in [
            ([], "numpy"),
            (["numpy<2"], "numpy<2"),
            (["numpy>=2; python_version >= '3.0'", "numpy<2; python_version < '3.0'"], "numpy>=2"),
        ]:
            with self.subTest(dependencies=dependencies):
                # Arrange
                metadata = self.root / "tritonserver-2.70.0.dist-info"
                metadata.mkdir(exist_ok=True)
                (metadata / "METADATA").write_text(
                    "Metadata-Version: 2.1\nName: tritonserver\nVersion: 2.70.0\n"
                    + "".join(f"Requires-Dist: {item}\n" for item in dependencies)
                )
                env = {**os.environ, "PYTHONPATH": str(self.root)}

                # Act
                result = subprocess.run(
                    [sys.executable, "-c", NUMPY_REQUIREMENT_SCRIPT],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )

                # Assert
                self.assertEqual(result.stdout.strip(), expected)

    def test_generic_image_does_not_force_numpy_one(self):
        # Arrange
        self.environment["TEST_NUMPY_REQUIREMENT"] = "numpy"

        # Act
        result = self.run_setup()
        installs = [line for line in (self.root / "log").read_text().splitlines() if "-m pip install" in line]

        # Assert
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("numpy<2", installs[0])

    def test_numpy_repair_backs_up_only_broken_user_installation(self):
        for broken in (True, False):
            with self.subTest(broken=broken), tempfile.TemporaryDirectory() as folder:
                # Arrange
                user_base = Path(folder)
                user_site = user_base / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
                user_site.mkdir(parents=True)
                names = ("numpy", "numpy.libs", "numpy-1.26.4.dist-info", "numpy-2.2.6.dist-info")
                for name in names:
                    (user_site / name).mkdir()
                    (user_site / name / "original").write_text(name)
                (user_site / "numpy/__init__.py").write_text(
                    "raise RuntimeError('Mixed NumPy installation')" if broken else "def dtype(value): return value"
                )
                (user_site / "keep.txt").write_text("Other workspace files")
                env = {**os.environ, "PYTHONUSERBASE": folder, "PYTHONPATH": str(user_site)}

                # Act
                result = subprocess.run(
                    [sys.executable, "-c", NUMPY_REPAIR_SCRIPT],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )

                # Assert
                backups = list(user_site.parent.glob("numpy-repair-*"))
                self.assertEqual(len(backups), int(broken))
                for name in names:
                    self.assertEqual((user_site / name).exists(), not broken)
                    if broken:
                        self.assertEqual((backups[0] / name / "original").read_text(), name)
                self.assertEqual((user_site / "keep.txt").read_text(), "Other workspace files")
                self.assertEqual("Backed up broken NumPy" in result.stderr, broken)

    def test_package_upgrade_removes_old_distribution_metadata(self):
        # Arrange
        package = "workspace_upgrade_fixture"
        wheels = []
        for version in ("1.0", "2.0"):
            metadata = f"{package}-{version}.dist-info"
            files = {
                f"{package}/__init__.py": f"__version__ = '{version}'\n",
                f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n",
                f"{metadata}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            }
            files[f"{metadata}/RECORD"] = "".join(f"{name},,\n" for name in files) + f"{metadata}/RECORD,,\n"
            wheel = self.root / f"{package}-{version}-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name, content in files.items():
                    archive.writestr(name, content)
            wheels.append(wheel)
        env = {
            **os.environ,
            "PYTHONUSERBASE": str(self.root / "python-user"),
            "PYTHONPATH": "",
            "PIP_BREAK_SYSTEM_PACKAGES": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        python = str(Path(sys.base_prefix) / "bin/python3")
        probe = (
            f"import json, {package}; from importlib.metadata import distributions; "
            f"print(json.dumps([{package}.__version__, "
            f"[d.version for d in distributions() if d.metadata['Name'] == '{package}']]))"
        )

        # Act
        for wheel in wheels:
            subprocess.run(
                [python, "-m", "pip", "install", *USER_INSTALL_FLAGS, "--no-index", "--no-deps", str(wheel)],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        result = subprocess.run([python, "-c", probe], env=env, capture_output=True, text=True, check=True)

        # Assert
        self.assertEqual(json.loads(result.stdout), ["2.0", ["2.0"]])

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
