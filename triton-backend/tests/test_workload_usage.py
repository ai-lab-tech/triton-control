from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from kubernetes.client import ApiException  # type: ignore[import-untyped]

from app.services.deployment import kubernetes


class WorkloadUsageTests(TestCase):
    def test_sums_only_triton_containers_and_requires_complete_replica_samples(self):
        limits = SimpleNamespace(limits={"cpu": "1", "memory": "1Gi"})
        spec = SimpleNamespace(containers=[SimpleNamespace(name="triton", resources=limits)])
        pods = [SimpleNamespace(metadata=SimpleNamespace(name=name), spec=spec) for name in ("a", "b")]
        samples = [
            {"metadata": {"name": name}, "containers": [
                {"name": "sidecar", "usage": {"cpu": "9", "memory": "1Gi"}},
                {"name": "triton", "usage": {"cpu": "500000n", "memory": "1Mi"}},
            ]}
            for name in ("a", "b")
        ]
        with (
            patch.object(kubernetes, "_client"),
            patch("kubernetes.client.CoreV1Api") as core,
            patch("kubernetes.client.CustomObjectsApi") as metrics,
        ):
            core.return_value.list_namespaced_pod.return_value.items = pods
            read = metrics.return_value.list_namespaced_custom_object
            read.return_value = {"items": samples}
            self.assertEqual(kubernetes.read_workload_usage("team", "test"), {
                "cpu_millicores": 1, "memory_bytes": 2 * 1024**2,
                "cpu_limit_millicores": 2000, "memory_limit_bytes": 2 * 1024**3,
            })
            self.assertEqual(read.call_args.kwargs["label_selector"], "app=triton,deployment=test")
            del samples[1]["containers"][1]["usage"]["memory"]
            partial = kubernetes.read_workload_usage("team", "test")
            self.assertEqual(partial["cpu_millicores"], 1)
            self.assertNotIn("memory_bytes", partial)
            self.assertIn("workload_error", partial)
            read.side_effect = ApiException(status=403)
            self.assertIn("Permission denied", kubernetes.read_workload_usage("team", "test")["workload_error"])
