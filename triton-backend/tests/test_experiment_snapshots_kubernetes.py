"""Real Kubernetes observation and bounded workspace exec in one disposable namespace."""

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from kubernetes import client, config  # type: ignore[import-untyped]

from app.exceptions import BadRequestError, ConflictError
from app.services.deployment.pod_provider import PodProvider
from app.services.snapshots.runtime import RuntimeProvider
from app.services.snapshots.workspace import WorkspaceProvider


@unittest.skipUnless(os.getenv("PODS_TEST_KUBECONFIG"), "Requires an explicitly selected Kubernetes test cluster")
class SnapshotKubernetesTests(unittest.TestCase):
    def test_actual_owned_runtime_and_selected_workspace_bytes(self):
        kubeconfig = os.environ["PODS_TEST_KUBECONFIG"]
        config.load_kube_config(config_file=kubeconfig)
        with client.ApiClient() as api:
            provider = PodProvider(api)
            namespace = "tc-snapshots-" + uuid4().hex[:8]
            provider.core.create_namespace({"metadata": {"name": namespace}})
            self.addCleanup(lambda: provider.core.delete_namespace(namespace))
            image = os.getenv("SNAPSHOTS_TEST_IMAGE", "python:3.12-slim")
            template = {
                "metadata": {"labels": {"test": "serving"}},
                "spec": {
                    "terminationGracePeriodSeconds": 1,
                    "containers": [{"name": "triton", "image": image, "command": ["sleep", "3600"]}],
                },
            }
            provider.apps.create_namespaced_deployment(
                namespace,
                {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {"name": "experiment"},
                    "spec": {"replicas": 1, "selector": {"matchLabels": {"test": "serving"}}, "template": template},
                },
            )
            provider.apps.create_namespaced_stateful_set(
                namespace,
                {
                    "apiVersion": "apps/v1",
                    "kind": "StatefulSet",
                    "metadata": {"name": "workspace"},
                    "spec": {
                        "serviceName": "workspace",
                        "replicas": 1,
                        "selector": {"matchLabels": {"test": "workspace"}},
                        "template": {
                            "metadata": {"labels": {"test": "workspace"}},
                            "spec": {
                                "terminationGracePeriodSeconds": 1,
                                "containers": [
                                    {
                                        "name": "code-server",
                                        "image": image,
                                        "command": [
                                            "sh",
                                            "-c",
                                            "mkdir -p /workspace/project; "
                                            "printf uncommitted > /workspace/project/model.py; "
                                            "printf private > /workspace/.env; sleep 3600",
                                        ],
                                        "volumeMounts": [{"name": "workspace", "mountPath": "/workspace"}],
                                    }
                                ],
                                "volumes": [{"name": "workspace", "emptyDir": {}}],
                            },
                        },
                    },
                },
            )
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                state, pods = provider.snapshot(namespace, "experiment")
                workspace = provider.apps.read_namespaced_stateful_set("workspace", namespace)
                if state.ready == 1 and not state.stale and workspace.status.ready_replicas == 1:
                    break
                time.sleep(2)
            else:
                self.fail("Disposable snapshot fixtures did not become ready")
            observed = RuntimeProvider().read(provider, namespace, "experiment")
            self.assertEqual(observed.deployment_uid, state.uid)
            self.assertEqual({item.pod_uid for item in observed.images}, {item.uid for item in pods})
            self.assertTrue(all(item.platform for item in observed.images))
            self.assertTrue(all(item.declared == image for item in observed.images))
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.dict(os.environ, {"KUBERNETES_KUBECONFIG_PATH": kubeconfig}),
            ):
                inventory = WorkspaceProvider().capture(namespace, "workspace", ("project/model.py",), Path(directory))
                self.assertEqual((Path(directory) / ".workspace/project/model.py").read_bytes(), b"uncommitted")
                self.assertEqual(len(inventory.files), 1)
                with self.assertRaises(BadRequestError):
                    WorkspaceProvider().capture(namespace, "workspace", (".env",), Path(directory))
                with patch("app.services.snapshots.workspace.controlled_by", return_value=False):
                    with self.assertRaises(ConflictError):
                        WorkspaceProvider().capture(namespace, "workspace", ("project/model.py",), Path(directory))
