from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from kubernetes.client import ApiException  # type: ignore[import-untyped]

from app.services.deployment import kubernetes


class WorkloadUsageTests(TestCase):
    def test_sums_only_triton_containers_and_requires_complete_replica_samples(self):
        limits = {name: {"cpu": "1", "memory": "1Gi"} for name in ("a", "b")}
        samples = [
            {"metadata": {"name": name}, "containers": [
                {"name": "sidecar", "usage": {"cpu": "9", "memory": "1Gi"}},
                {"name": "triton", "usage": {"cpu": "500000n", "memory": "1Mi"}},
            ]}
            for name in ("a", "b", "foreign")
        ]
        with patch("kubernetes.client.CustomObjectsApi") as metrics:
            read = metrics.return_value.list_namespaced_custom_object
            read.return_value = {"items": samples}
            self.assertEqual(kubernetes.read_workload_usage(None, "team", "test", limits), {
                "cpu_samples": 2, "memory_samples": 2,
                "cpu_millicores": 1, "memory_bytes": 2 * 1024**2,
                "cpu_limit_millicores": 2000, "memory_limit_bytes": 2 * 1024**3,
            })
            self.assertEqual(read.call_args.kwargs["label_selector"], "app=triton,deployment=test")
            del samples[1]["containers"][1]["usage"]["memory"]
            partial = kubernetes.read_workload_usage(None, "team", "test", limits)
            self.assertEqual(partial["cpu_millicores"], 1)
            self.assertNotIn("memory_bytes", partial)
            self.assertIn("workload_error", partial)
            read.side_effect = ApiException(status=403)
            denied = kubernetes.read_workload_usage(None, "team", "test", limits)
            self.assertIn("Permission denied", denied["workload_error"])

    def test_running_sidecar_does_not_make_waiting_triton_eligible(self):
        def pod(name, state):
            containers = [
                SimpleNamespace(name="triton", kind="regular", state=state, limits={}),
                SimpleNamespace(name="sidecar", kind="regular", state="running", limits={}),
            ]
            return SimpleNamespace(name=name, ready=state == "running", phase="Running", containers=containers)

        pods = [pod("healthy", "running"), pod("restarting", "waiting")]
        with (
            patch.object(kubernetes, "_client") as api,
            patch("app.services.deployment.pod_provider.PodProvider") as provider,
            patch.object(kubernetes, "read_workload_usage", return_value={"cpu_samples": 1}) as read,
        ):
            provider.return_value.workload_snapshot.return_value = SimpleNamespace(desired=2), pods, {"healthy": {}}
            statuses, ready, usage = kubernetes.read_workload_health("team", "test")

        provider.return_value.workload_snapshot.assert_called_once_with("team", "test")
        read.assert_called_once_with(api.return_value.__enter__.return_value, "team", "test", {"healthy": {}})
        self.assertTrue(ready)
        self.assertEqual(statuses, ["healthy: Running (Ready)", "restarting: Running (Not Ready)"])
        self.assertEqual(usage, {"desired_replicas": 2, "running_triton_containers": 1, "cpu_samples": 1})
