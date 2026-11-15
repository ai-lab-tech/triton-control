"""Opt-in real Kubernetes contract test; creates and deletes only its own namespace.

PODS_TEST_KUBECONFIG=/path/to/disposable/kubeconfig python -m unittest tests.test_pods_kubernetes -v
"""

import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from kubernetes import client, config  # type: ignore[import-untyped]

from app.exceptions import NotFoundError
from app.services.deployment.pod_provider import PodProvider


@unittest.skipUnless(os.getenv("PODS_TEST_KUBECONFIG"), "Requires an explicitly selected Kubernetes test cluster")
class RealPodProviderTests(unittest.TestCase):
    def test_owned_state_current_previous_init_sidecar_and_replacement(self):
        config.load_kube_config(config_file=os.environ["PODS_TEST_KUBECONFIG"])
        with client.ApiClient() as api:
            reader = PodProvider(api)
            ns = "tc-pods-test-" + uuid4().hex[:8]
            reader.core.create_namespace({"metadata": {"name": ns}})
            self.addCleanup(lambda: reader.core.delete_namespace(ns))
            image = os.getenv("PODS_TEST_IMAGE", "busybox:1.37")
            spec = {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "metadata": {"name": "serving"},
                "spec": {
                    "replicas": 1,
                    "selector": {"matchLabels": {"test": "serving"}},
                    "template": {
                        "metadata": {
                            "labels": {"test": "serving"},
                            "annotations": {"triton-control/release-id": "test-r1"},
                        },
                        "spec": {
                            "terminationGracePeriodSeconds": 1,
                            "volumes": [{"name": "state", "emptyDir": {}}],
                            "initContainers": [
                                {"name": "download", "image": image, "command": ["sh", "-c", "echo init-complete"]},
                                {
                                    "name": "sync",
                                    "image": image,
                                    "restartPolicy": "Always",
                                    "command": ["sh", "-c", "echo sidecar-started; sleep 3600"],
                                },
                            ],
                            "containers": [
                                {
                                    "name": "triton",
                                    "image": image,
                                    "volumeMounts": [{"name": "state", "mountPath": "/state"}],
                                    "command": [
                                        "sh",
                                        "-c",
                                        "if [ ! -f /state/restarted ]; then touch /state/restarted; "
                                        "echo before-restart; exit 1; fi; echo runtime-ready; sleep 3600",
                                    ],
                                }
                            ],
                        },
                    },
                },
            }
            reader.apps.create_namespaced_deployment(ns, spec)

            def wait_for(check, timeout=180):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    state, items = reader.snapshot(ns, "serving")
                    if check(state, items):
                        return state, items
                    time.sleep(2)
                self.fail("Kubernetes test workload did not reach expected state")

            state, items = wait_for(
                lambda d, ps: d.available == 1 and not d.stale and len(ps) == 1 and ps[0].ready and ps[0].restarts >= 1
            )
            pod = items[0]
            self.assertEqual(pod.release, "test-r1")
            self.assertEqual([c.kind for c in pod.containers], ["init", "sidecar", "regular"])
            for container, previous, expected in [
                ("download", False, "init-complete"),
                ("sync", False, "sidecar-started"),
                ("triton", False, "runtime-ready"),
                ("triton", True, "before-restart"),
            ]:
                text, _ = reader.logs(
                    ns,
                    "serving",
                    state.uid,
                    pod,
                    container,
                    previous,
                    datetime.now(timezone.utc) - timedelta(minutes=10),
                    100,
                    4096,
                )
                self.assertIn(expected, text)
                self.assertRegex(text, r"^\d{4}-\d{2}-\d{2}T")

            # Same labels, but an unrelated controller UID: never include this pod.
            cm = reader.core.create_namespaced_config_map(ns, {"metadata": {"name": "other-owner"}})
            reader.core.create_namespaced_pod(
                ns,
                {
                    "metadata": {
                        "name": "unrelated",
                        "labels": {"test": "serving"},
                        "ownerReferences": [
                            {
                                "apiVersion": "v1",
                                "kind": "ConfigMap",
                                "name": "other-owner",
                                "uid": cm.metadata.uid,
                                "controller": True,
                            }
                        ],
                    },
                    "spec": {"containers": [{"name": "unrelated", "image": image, "command": ["sleep", "3600"]}]},
                },
            )
            self.assertNotIn("unrelated", [p.name for p in reader.snapshot(ns, "serving")[1]])

            reader.apps.patch_namespaced_deployment(
                "serving",
                ns,
                {"spec": {"template": {"metadata": {"annotations": {"triton-control/release-id": "test-r2"}}}}},
            )
            new_state, new_pods = wait_for(
                lambda d, ps: (
                    d.available == 1
                    and not d.stale
                    and len(ps) == 1
                    and ps[0].uid != pod.uid
                    and ps[0].ready
                    and ps[0].restarts >= 1
                )
            )
            self.assertNotEqual(new_pods[0].uid, pod.uid)
            self.assertEqual(new_pods[0].release, "test-r2")
            with self.assertRaises((NotFoundError, client.rest.ApiException)):
                reader.logs(ns, "serving", new_state.uid, pod, "triton", False, None, 100, 4096)

            # Real scheduling evidence on a cluster with no such resource.
            reader.apps.patch_namespaced_deployment(
                "serving",
                ns,
                {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {"name": "triton", "resources": {"limits": {"example.com/unavailable-gpu": "1"}}}
                                ]
                            }
                        }
                    }
                },
            )
            _, pending = wait_for(lambda d, ps: any(p.reason == "Unschedulable" for p in ps))
            self.assertTrue(any("unavailable-gpu" in (p.message or "") for p in pending))
