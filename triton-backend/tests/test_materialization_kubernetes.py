"""Run the actual digest-pinned helper as an init container on real Kubernetes.

A Python probe replaces Triton: these tests qualify materialization and the
read-only mount, not inference correctness or release approval.
"""

import copy
import json
import os
import time
import unittest
from pathlib import Path
from uuid import uuid4

from kubernetes import client, config  # type: ignore[import-untyped]

from app.core.canonical import inventory_digest
from app.schemas.materialization import StorageBinding
from app.services.materialization.kubernetes import manifests
from tests import test_materialization as unit
from tests.materialization_fixtures import FILES, LIMITS, plan, policy

IMAGE = os.getenv("MATERIALIZER_TEST_IMAGE", "")
KUBECONFIG = os.getenv("MATERIALIZER_TEST_KUBECONFIG", "")
PLATFORM = os.getenv("MATERIALIZER_TEST_PLATFORM", "")


@unittest.skipUnless(IMAGE and KUBECONFIG and PLATFORM, "Requires pinned helper, explicit kubeconfig and platform")
class MaterializerKubernetesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.load_kube_config(config_file=KUBECONFIG)
        cls.api = client.ApiClient()
        cls.addClassCleanup(cls.api.close)
        cls.core = client.CoreV1Api(cls.api)
        cls.ns = "tc-materialize-" + uuid4().hex[:10]
        cls.core.create_namespace({"metadata": {"name": cls.ns}})
        cls.addClassCleanup(lambda: cls.core.delete_namespace(cls.ns))
        cls.core.create_namespaced_config_map(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "data": {
                    "server.py": Path(__file__).with_name("materializer_s3_server.py").read_text(),
                    "files.json": json.dumps({key: value.decode() for key, value in FILES.items()}),
                },
            },
        )
        cls.core.create_namespaced_secret(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "stringData": {
                    "AWS_ACCESS_KEY_ID": "synthetic-fixture",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-fixture",
                },
            },
        )
        cls.core.create_namespaced_service(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "spec": {
                    "selector": {"test": "s3"},
                    "ports": [{"port": 8080, "targetPort": 8080}],
                },
            },
        )
        cls.core.create_namespaced_pod(
            cls.ns,
            {
                "metadata": {"name": "fixture", "labels": {"test": "s3"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "s3",
                            "image": IMAGE,
                            "command": ["python", "/fixture/server.py"],
                            "volumeMounts": [{"name": "fixture", "mountPath": "/fixture", "readOnly": True}],
                            "readinessProbe": {"httpGet": {"path": "/health", "port": 8080}, "periodSeconds": 1},
                        }
                    ],
                    "volumes": [{"name": "fixture", "configMap": {"name": "fixture"}}],
                },
            },
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pod = cls.core.read_namespaced_pod("fixture", cls.ns)
            if pod.status.container_statuses and all(c.ready for c in pod.status.container_statuses):
                return
            time.sleep(1)
        raise AssertionError("S3 fixture did not start: " + str(pod.status))

    def execute(self, case, *, valid=False, followup=None):
        desired = plan(IMAGE)
        if case == "normalization":
            payload = desired.model_dump(mode="json")
            payload["normalization"]["rewrites"][0]["relative"] = "does-not-exist"
            desired = type(desired).model_validate(payload)
        if case == "prepared-hash":
            payload = desired.model_dump(mode="json")
            payload["prepared_inventory"]["files"][0]["digest"] = "sha256:" + "0" * 64
            desired = type(desired).model_validate(payload)
        release = unit.MaterializerPodTests().release()
        repository = release.spec.repository.model_copy(
            update={
                "uri": f"s3://fixture/{case}",
                "materializer_image": IMAGE,
                "inventory_digest": inventory_digest(desired.source_inventory),
                "prepared_inventory_digest": inventory_digest(desired.prepared_inventory),
            }
        )
        release = release.model_copy(update={"spec": release.spec.model_copy(update={"repository": repository})})
        resources = manifests(
            release,
            desired,
            policy(IMAGE, PLATFORM),
            StorageBinding(
                bucket="fixture", prefix=case, secret_name="fixture", endpoint="http://fixture:8080", allow_http=True
            ),
            name=case + "-" + uuid4().hex[:6],
            namespace=self.ns,
            limits=LIMITS,
        )
        cm, pod = resources
        pod["spec"]["restartPolicy"] = "Never"
        pod["spec"]["terminationGracePeriodSeconds"] = 1
        main = pod["spec"]["containers"][0]
        main["image"] = IMAGE
        main["resources"] = {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}}
        main["command"] = ["python", "-c"]
        main["args"] = [
            """
import errno, json, os
from pathlib import Path
assert Path('/models/iris/1/weights/model.bin').read_bytes() == b'synthetic model fixture'
assert json.loads(Path('/models/iris/1/model.json').read_text())['model'] == '/models/iris/1/weights'
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
try:
    Path('/models/unexpected').write_text('forbidden')
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError('model repository was writable')
print(Path('/var/run/triton-materialization/receipt.json').read_text())
"""
        ]
        init = pod["spec"]["initContainers"][0]
        if followup in {"restart", "changed-helper", "retained-corruption"}:
            repeated = copy.deepcopy(init)
            repeated["name"] = "reverify-models"
            if followup == "changed-helper":
                # Run the actual image with a changed declared helper; retained receipts must reject it.
                other = "fixture.invalid/changed@sha256:" + "0" * 64
                changed = desired.model_dump(mode="json")
                changed["materializer_image"] = other
                cm["data"]["changed.json"] = json.dumps(changed)
                repeated["args"][repeated["args"].index("--plan") + 1] = "/plan/changed.json"
                repeated["args"][repeated["args"].index("--expected-image") + 1] = other
            else:
                # A correct retained-volume retry must not contact storage at all.
                repeated["args"][repeated["args"].index("--endpoint") + 1] = "http://127.0.0.1:1"
            if followup == "retained-corruption":
                mutate = copy.deepcopy(init)
                mutate.update(
                    name="corrupt-retained",
                    command=["python", "-c"],
                    args=[
                        "from pathlib import Path; p=Path('/materialized/ready/models/iris/1/weights/model.bin'); "
                        "p.chmod(0o644); p.write_bytes(b'corrupt retained bytes')"
                    ],
                )
                pod["spec"]["initContainers"].append(mutate)
            pod["spec"]["initContainers"].append(repeated)
        elif followup == "interrupted-recovery":
            interrupted = copy.deepcopy(init)
            args = list(init["args"])
            args[args.index("--prefix") + 1] = "interrupted"
            interrupted.update(
                name="interrupt-download",
                command=["python", "-c"],
                args=[
                    "import subprocess,sys; from pathlib import Path; "
                    "result=subprocess.run([sys.executable,'-m','app.services.materialization.cli']+"
                    + repr(args)
                    + "); assert result.returncode == 1; "
                    "root=Path('/materialized'); assert not (root/'ready').exists(); "
                    "stale=root/'.staging-crashed'; stale.mkdir(); (stale/'partial').write_bytes(b'partial')"
                ],
            )
            verify = copy.deepcopy(init)
            verify.update(
                name="verify-recovery",
                command=["python", "-c"],
                args=["from pathlib import Path; assert not list(Path('/materialized').glob('.staging-*'))"],
            )
            pod["spec"]["initContainers"] = [interrupted, init, verify]
        self.core.create_namespaced_config_map(self.ns, cm)
        self.core.create_namespaced_pod(self.ns, pod)
        name = pod["metadata"]["name"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            actual = self.core.read_namespaced_pod(name, self.ns)
            if actual.status.phase in {"Succeeded", "Failed"}:
                break
            time.sleep(1)
        logs = self.core.read_namespaced_pod_log(name, self.ns, container=pod["spec"]["initContainers"][-1]["name"])
        if valid:
            self.assertEqual(actual.status.phase, "Succeeded", str(actual.status) + "\n" + logs)
            response = self.core.read_namespaced_pod_log(name, self.ns, container="triton", _preload_content=False)
            result = json.loads(response.data.decode())
            self.assertEqual(result["prepared_inventory_digest"], inventory_digest(desired.prepared_inventory))
            self.assertEqual(result["materializer_image"], IMAGE)
            return result
        self.assertEqual(actual.status.phase, "Failed", str(actual.status) + "\n" + logs)
        self.assertNotEqual(actual.status.init_container_statuses[-1].state.terminated.exit_code, 0)
        self.assertIn("Materialization failed:", logs)
        self.assertNotIn("Traceback", logs)
        self.assertFalse(any(c.state.running or c.state.terminated for c in actual.status.container_statuses or []))
        return None

    def test_pinned_init_readonly_runtime_and_recreated_pod(self):
        # Arrange
        case = "valid"
        # Act
        first = self.execute(case, valid=True)
        second = self.execute(case, valid=True)
        # Assert
        self.assertEqual(first, second)

    def test_retained_volume_restart_without_storage(self):
        # Arrange
        case = "valid"
        # Act
        receipt = self.execute(case, valid=True, followup="restart")
        # Assert
        self.assertEqual(receipt["materializer_image"], IMAGE)

    def test_changed_helper_cannot_adopt_retained_volume(self):
        # Arrange
        case = "valid"
        # Act
        result = self.execute(case, followup="changed-helper")
        # Assert
        self.assertIsNone(result)

    def test_corrupt_retained_volume_blocks_restart(self):
        # Arrange
        case = "valid"
        # Act
        result = self.execute(case, followup="retained-corruption")
        # Assert
        self.assertIsNone(result)

    def test_interrupted_download_recovers_on_same_volume(self):
        # Arrange
        case = "valid"
        # Act
        receipt = self.execute(case, valid=True, followup="interrupted-recovery")
        # Assert
        self.assertEqual(receipt["materializer_image"], IMAGE)

    def test_rejects_missing(self):
        # Arrange
        case = "missing"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_extra(self):
        # Arrange
        case = "extra"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_unsafe(self):
        # Arrange
        case = "unsafe"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_version(self):
        # Arrange
        case = "version"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_corrupt(self):
        # Arrange
        case = "corrupt"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_interrupted(self):
        # Arrange
        case = "interrupted"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_normalization(self):
        # Arrange
        case = "normalization"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_prepared_hash(self):
        # Arrange
        case = "prepared-hash"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)
