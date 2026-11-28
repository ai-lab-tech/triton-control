from types import SimpleNamespace as Obj
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from app.services.deployment.managed_metrics import ManagedMetricsService, trusted_metrics_url
from app.services.triton.client import TritonService


class ManagedMetricsTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = "http://iris-service.team.svc.cluster.local:18002/metrics"
        self.instance = Obj(
            id=7,
            deployment_runtime="kubernetes",
            is_self_deployed=True,
            deployment_namespace="team",
            deployment_name="iris",
            deployment_service_name="iris-service",
        )
        self.service = TritonService("http://iris")
        self.enterContext(patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": ""}))
        module = "app.services.deployment.managed_metrics"
        self.intent = self.enterContext(patch(module + ".deployment_intents.for_instance", return_value=None))
        self.enterContext(patch(module + ".api_client"))
        core = self.enterContext(patch(module + ".client.CoreV1Api")).return_value
        apps = self.enterContext(patch(module + ".client.AppsV1Api")).return_value
        selector = {"app": "triton", "deployment": "iris"}
        port = Obj(name="metrics", protocol="TCP", target_port=8002, port=18002)
        self.spec = Obj(type="ClusterIP", cluster_ip="10.0.0.1", selector=selector, ports=[port])
        self.meta = Obj(owner_references=[], uid="svc", labels={}, deletion_timestamp=None)
        core.read_namespaced_service.return_value = Obj(metadata=self.meta, spec=self.spec)
        apps.read_namespaced_deployment.return_value = Obj(
            metadata=Obj(uid="dep", owner_references=[], labels={}, deletion_timestamp=None),
            spec=Obj(
                selector=Obj(match_labels=selector, match_expressions=None),
                template=Obj(
                    metadata=Obj(labels=selector),
                    spec=Obj(
                        containers=[
                            Obj(name="triton", ports=[Obj(name="metrics", container_port=8002, protocol="TCP")])
                        ]
                    ),
                ),
            ),
        )
        self.dep = apps.read_namespaced_deployment.return_value
        owner = Obj(controller=True, kind="Deployment", uid="dep")
        apps.list_namespaced_replica_set.return_value.items = [Obj(metadata=Obj(uid="rs", owner_references=[owner]))]
        self.pod_owner = Obj(controller=True, kind="ReplicaSet", uid="rs")
        core.list_namespaced_pod.return_value.items = [Obj(metadata=Obj(owner_references=[self.pod_owner]))]
        response = Mock(content=b"nv_gpu_utilization 0.25")
        self.client = Obj(get=AsyncMock(return_value=response))
        self.enterContext(patch.object(self.service, "_get_client", return_value=self.client))

    async def resolve(self, requested):
        identity = ManagedMetricsService.from_instance(Mock(), self.instance, requested)
        return await trusted_metrics_url(identity, requested)

    async def test_managed_gpu_and_inference_use_verified_service(self):
        trusted = await self.resolve(self.url)
        await self.service.collect_inference_metrics_snapshot(self.url, trusted_url=trusted)
        result = await self.service.collect_metrics_snapshot(self.url, trusted_url=trusted)
        self.assertEqual(result["resource_usage"]["gpu_percent"], 25)
        self.assertEqual(self.client.get.await_count, 2)
        self.client.get.assert_awaited_with(self.url, follow_redirects=False)

    async def test_external_and_edited_urls_require_exact_approval(self):
        custom = self.url + "?custom=true"
        trusted = await self.resolve(self.url)
        self.assertIsNone(await self.resolve(custom))
        self.assertIsNotNone((await self.service.collect_metrics_snapshot(custom, trusted_url=trusted))["error"])
        self.instance.is_self_deployed = False
        self.assertIsNone(await self.resolve(self.url))
        self.client.get.assert_not_awaited()
        with patch.dict("os.environ", {"TRITON_METRICS_ALLOWED_URLS": custom}):
            await self.service.collect_metrics_snapshot(custom)
        self.client.get.assert_awaited_once_with(custom, follow_redirects=False)

    async def test_foreign_pods_external_service_and_wrong_container_port(self):
        self.pod_owner.uid = "foreign"
        self.assertIsNone(await self.resolve(self.url))
        self.pod_owner.uid = "rs"
        self.spec.type = "ExternalName"
        self.assertIsNone(await self.resolve(self.url))
        self.spec.type = "ClusterIP"
        self.spec.ports[0].target_port = "metrics"
        self.assertEqual(await self.resolve(self.url), self.url)
        self.dep.spec.template.spec.containers[0].ports[0].container_port = 8000
        self.assertIsNone(await self.resolve(self.url))

    async def test_intent_requires_recorded_uids_and_operation_labels(self):
        intent = Obj(
            operation_id="op", namespace="team", name="iris", cleaned=False, delete_requested=False, resource_uids={}
        )
        self.intent.return_value = intent
        self.assertIsNone(await self.resolve(self.url))
        intent.resource_uids = {"Service/iris-service": "svc", "Deployment/iris": "dep"}
        self.assertIsNone(await self.resolve(self.url))
        self.meta.labels = self.dep.metadata.labels = {"triton-control.io/operation": "op"}
        self.assertEqual(await self.resolve(self.url), self.url)
        self.meta.uid = "replacement"
        self.assertIsNone(await self.resolve(self.url))
        self.meta.uid = "svc"
        self.dep.metadata.deletion_timestamp = "now"
        self.assertIsNone(await self.resolve(self.url))
