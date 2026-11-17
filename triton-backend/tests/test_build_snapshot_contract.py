"""The real Node extension archive must be accepted by the backend verifier."""

import base64
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.schemas.build_contexts import BuildContextRequest
from app.services.builds.contexts_archive import prepare


@unittest.skipUnless(shutil.which("node"), "Node required for extension/backend contract")
class ExtensionSnapshotTests(unittest.TestCase):
    def test_extension_snapshot_passes_backend_checks_and_dockerignore_filtering(self):
        # Arrange
        module = Path(__file__).resolve().parents[2] / "code-server-extensions/triton-deploy/build-snapshot.js"
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as scratch:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\nCOPY model /model\n")
            (root / "model").write_text("model bytes")
            (root / "debug.log").write_text("ignored by dockerignore")
            (root / ".dockerignore").write_text("*.log\n")
            (root / ".env").write_text("credential excluded locally")
            script = """require(process.argv[1]).snapshot(process.argv[2], 'Dockerfile', 10000)
                .then(x => process.stdout.write(JSON.stringify({
                    request:x.request, archive:x.archive.toString('base64')})))
                .catch(() => process.exit(1));"""
            # Act
            result = subprocess.run(["node", "-e", script, str(module), directory],
                                    capture_output=True, text=True, check=True, timeout=10)
            payload = json.loads(result.stdout)
            archive = base64.b64decode(payload["archive"])
            manifest = prepare(io.BytesIO(archive), Path(scratch), io.BytesIO(),
                               BuildContextRequest.model_validate(payload["request"]), 10000)
            # Assert
            self.assertEqual({item.path for item in manifest.files}, {"Dockerfile", "model", ".dockerignore"})
            self.assertNotIn(b"credential excluded locally", archive)
            self.assertEqual(manifest.base_images, ())

    def test_deployed_vsix_includes_all_serving_build_runtime_modules(self):
        # Arrange
        from app.services.development import kubernetes
        root = Path(__file__).resolve().parents[2] / "code-server-extensions/triton-deploy"
        package = json.loads((root / "package.json").read_text())
        # Act
        encoded = kubernetes._triton_deploy_extension_vsix_b64(root, package)
        # Assert
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
            for module in ("build-client.js", "build-snapshot.js", "serving-build.js"):
                self.assertEqual(archive.read("extension/" + module), (root / module).read_bytes())
            self.assertIn(b"registerServingBuild", archive.read("extension/extension.js"))
