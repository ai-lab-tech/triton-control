"""Exercise the generated installer with offline wheels and existing image packages."""

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.schemas import CreateDeploymentRequest
from app.services.deployment.kubernetes import _requirements_install_command


class ServingDependencyInstallTests(unittest.TestCase):
    def test_remote_wheel_source_is_accepted_and_passed_as_separate_arguments(self):
        requirements = (
            "--find-links https://download.pytorch.org/whl/cpu/torch/\n"
            "torch==2.14.1+cpu"
        )
        request = CreateDeploymentRequest(
            deployment_name="cpu-model",
            image="triton:cpu",
            s3_profile_id=1,
            model_name="model",
            repository_prefix="repository",
            model_uri="s3://bucket/repository/model",
            requirements_txt=requirements,
        )
        command = _requirements_install_command(request.requirements_txt)
        self.assertIn("--find-links https://download.pytorch.org/whl/cpu/torch/", command)
        self.assertNotIn("'--find-links https://", command)
        self.assertIn("torch==2.14.1+cpu", command)

    def test_unsafe_or_unrelated_install_options_are_rejected(self):
        for option in (
            "--find-links /tmp/wheels",
            "--find-links file:///tmp/wheels",
            "--find-links 'https://example.test/a b'",
            "--index-url https://example.test/simple",
            "--target /tmp/elsewhere",
            "--no-deps",
            "-r /tmp/requirements.txt",
        ):
            with self.subTest(option=option), self.assertRaises(ValueError):
                CreateDeploymentRequest(
                    deployment_name="cpu-model",
                    image="triton:cpu",
                    s3_profile_id=1,
                    model_name="model",
                    repository_prefix="repository",
                    model_uri="s3://bucket/repository/model",
                    requirements_txt=option,
                )

    def _run_install(self, installed_version, requested_version):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_site = root / "image-site"
            image_site.mkdir()
            (image_site / "fixture_dependency.py").write_text(f"VERSION = {installed_version!r}\n")
            distribution = image_site / f"fixture_dependency-{installed_version}.dist-info"
            distribution.mkdir()
            (distribution / "METADATA").write_text(
                f"Metadata-Version: 2.1\nName: fixture-dependency\nVersion: {installed_version}\n"
            )
            wheels = root / "wheels"
            wheels.mkdir()
            wheel = wheels / "fixture_dependency-2.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                prefix = "fixture_dependency-2.0.dist-info/"
                archive.writestr("fixture_dependency.py", "VERSION = '2.0'\n")
                archive.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-dependency\nVersion: 2.0\n")
                archive.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
                archive.writestr(prefix + "RECORD", "")

            runtime = root / "runtime"
            command = _requirements_install_command(f"fixture-dependency=={requested_version}")
            command = command.replace("/tmp/triton-python-env", str(runtime))
            command += (
                f"{runtime}/bin/python -c "
                "'import fixture_dependency; print(\"selected=\" + fixture_dependency.VERSION)'"
            )
            environment = {
                **os.environ,
                "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
                "PYTHONPATH": str(image_site),
                "PIP_NO_INDEX": "1",
                "PIP_FIND_LINKS": str(wheels),
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            }
            result = subprocess.run(
                ["bash", "-ec", command],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn(f"selected={requested_version}", result.stdout)
            self.assertEqual(
                (image_site / "fixture_dependency.py").read_text(),
                f"VERSION = {installed_version!r}\n",
            )
            installed = list(runtime.glob("lib/python*/site-packages/fixture_dependency-*.dist-info"))
            return result.stdout, len(installed)

    def test_matching_image_package_is_reused_without_installing_a_copy(self):
        output, installed = self._run_install("2.0", "2.0")
        self.assertIn("Requirement already satisfied", output)
        self.assertEqual(installed, 0)

    def test_different_version_is_installed_in_overlay_without_changing_image(self):
        output, installed = self._run_install("1.0", "2.0")
        self.assertIn("Successfully installed fixture-dependency-2.0", output)
        self.assertEqual(installed, 1)
