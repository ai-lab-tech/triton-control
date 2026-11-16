"""Run the actual digest-pinned helper as an init container on real Kubernetes.

A Python probe replaces Triton: these tests qualify materialization and the
read-only mount, not inference correctness or release approval.
"""

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
from tests.materialization_fixtures import FILES, plan, policy

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

    def execute(self, case, *, valid=False):
        desired = plan(IMAGE)
        if case == "normalization":
            payload = desired.model_dump(mode="json")
            payload["normalization"]["rewrites"][0]["relative"] = "does-not-exist"
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
        self.core.create_namespaced_config_map(self.ns, cm)
        self.core.create_namespaced_pod(self.ns, pod)
        name = pod["metadata"]["name"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            actual = self.core.read_namespaced_pod(name, self.ns)
            if actual.status.phase in {"Succeeded", "Failed"}:
                break
            time.sleep(1)
        logs = self.core.read_namespaced_pod_log(name, self.ns, container="materialize-models")
        if valid:
            self.assertEqual(actual.status.phase, "Succeeded", str(actual.status) + "\n" + logs)
            response = self.core.read_namespaced_pod_log(name, self.ns, container="triton", _preload_content=False)
            result = json.loads(response.data.decode())
            self.assertEqual(result["prepared_inventory_digest"], inventory_digest(desired.prepared_inventory))
            self.assertEqual(result["materializer_image"], IMAGE)
            return result
        self.assertEqual(actual.status.phase, "Failed", str(actual.status) + "\n" + logs)
        self.assertNotEqual(actual.status.init_container_statuses[0].state.terminated.exit_code, 0)
        self.assertIn('Materialization failed:', logs)
        self.assertNotIn('Traceback', logs)
        self.assertFalse(any(c.state.running or c.state.terminated for c in actual.status.container_statuses or []))
        return None

    def test_pinned_init_readonly_runtime_and_recreated_pod(self):
        first = self.execute("valid", valid=True)
        self.assertEqual(first, self.execute("valid", valid=True))

    def test_failed_initialization_never_starts_runtime(self):
        for case in ("missing", "extra", "unsafe", "version", "corrupt", "interrupted", "normalization"):
            with self.subTest(case=case):
                self.execute(case)
