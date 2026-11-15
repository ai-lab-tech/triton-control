"""Arrange–Act–Assert coverage of ownership and authorization boundaries."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.exceptions import BadGatewayError, ConflictError, ForbiddenError
from app.services.deployment.pod_provider import owned_state, read_owned_state
from app.services.deployment.pods import get_pods


def owner(kind, uid, controller=True):
    return {"kind": kind, "uid": uid, "controller": controller}


def resource(uid, kind, parent):
    return {"metadata": {"name": uid, "uid": uid, "ownerReferences": [owner(kind, parent)]}}


class PodStateTests(unittest.TestCase):
    def test_uid_chain_excludes_matching_labels_and_non_controller_owners(self):
        # Arrange
        deployment = {
            "metadata": {"uid": "d", "generation": 2},
            "spec": {"replicas": 3},
            "status": {"observedGeneration": 1, "readyReplicas": 2},
        }
        old, new, unrelated = [
            resource(uid, "Deployment", parent) for uid, parent in [("old", "d"), ("new", "d"), ("other", "foreign")]
        ]
        pods = [
            resource(uid, "ReplicaSet", parent)
            for uid, parent in [("old-pod", "old"), ("new-pod", "new"), ("foreign", "other"), ("orphan", "d")]
        ]
        pods.append(resource("non-controller", "ReplicaSet", "new"))
        pods[-1]["metadata"]["ownerReferences"][0]["controller"] = False
        pods[0]["metadata"]["deletionTimestamp"] = "2026-11-15T12:00:00Z"
        pods[1]["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "False"}]}
        # Act
        summary, result = owned_state(deployment, [old, new, unrelated], pods)
        # Assert
        self.assertEqual({p.uid for p in result}, {"old-pod", "new-pod"})
        self.assertTrue(summary.awaiting_observation)
        self.assertEqual((summary.desired, summary.ready), (3, 2))
        self.assertFalse(result[0].ready)
        self.assertIsNotNone(result[1].deletion_timestamp)
        self.assertIsNone(result[0].release)

    def test_scheduling_init_and_image_pull_evidence_are_preserved(self):
        # Arrange
        deployment = {"metadata": {"uid": "d", "generation": 1}, "status": {"observedGeneration": 1}}
        pod = resource("p", "ReplicaSet", "rs")
        pod["spec"] = {
            "containers": [{"name": "triton", "image": "runtime"}],
            "initContainers": [{"name": "download", "image": "helper"}],
        }
        pod["status"] = {
            "phase": "Pending",
            "conditions": [
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "Insufficient nvidia.com/gpu",
                }
            ],
            "containerStatuses": [
                {"name": "triton", "restartCount": 2, "state": {"waiting": {"reason": "ImagePullBackOff"}}}
            ],
            "initContainerStatuses": [
                {"name": "download", "state": {"terminated": {"reason": "Error", "exitCode": 1}}}
            ],
        }
        # Act
        summary, result = owned_state(deployment, [resource("rs", "Deployment", "d")], [pod])
        # Assert
        self.assertFalse(summary.awaiting_observation)
        self.assertEqual(result[0].conditions[0].reason, "Unschedulable")
        self.assertEqual(result[0].containers[0].reason, "ImagePullBackOff")
        self.assertEqual(result[0].containers[0].restarts, 2)
        self.assertEqual(result[0].containers[1].exit_code, 1)

    def test_access_denied_before_provider_is_called(self):
        # Arrange
        with (
            patch("app.services.deployment.pods.get_instance_or_404", side_effect=ForbiddenError("denied")),
            patch("app.services.deployment.pods.read_owned_state") as provider,
        ):
            # Act / Assert
            with self.assertRaises(ForbiddenError):
                get_pods(None, {}, 1)
            provider.assert_not_called()

    def test_external_endpoint_has_no_fabricated_kubernetes_state(self):
        # Arrange
        instance = SimpleNamespace(deployment_runtime="external", health_live=True, health_ready=False)
        with (
            patch("app.services.deployment.pods.get_instance_or_404", return_value=instance),
            patch("app.services.deployment.pods.read_owned_state") as provider,
        ):
            # Act
            result = get_pods(None, {}, 1)
            # Assert
            provider.assert_not_called()
            self.assertFalse(result.kubernetes_available)
            self.assertIsNone(result.deployment)
            self.assertEqual(result.pods, [])
            self.assertTrue(result.health_live)

    def test_provider_failure_is_not_an_empty_success(self):
        # Arrange
        with patch("app.services.deployment.pod_provider.api_client", side_effect=RuntimeError("private config")):
            # Act / Assert
            with self.assertRaisesRegex(BadGatewayError, "observation unavailable"):
                read_owned_state("ns", "deployment")

    def test_generation_or_uid_change_during_read_requires_refresh(self):
        # Arrange
        from unittest.mock import MagicMock

        for uid, generation in [("replacement", 1), ("d", 2)]:
            with self.subTest(uid=uid, generation=generation):
                api = MagicMock()
                api.sanitize_for_serialization.side_effect = [
                    {"metadata": {"uid": "d", "generation": 1}},
                    {"items": []},
                    {"items": []},
                ]
                latest = SimpleNamespace(metadata=SimpleNamespace(uid=uid, generation=generation))
                with (
                    patch("app.services.deployment.pod_provider.api_client") as context,
                    patch("app.services.deployment.pod_provider.client.AppsV1Api") as apps,
                ):
                    context.return_value.__enter__.return_value = api
                    apps.return_value.read_namespaced_deployment.side_effect = [object(), latest]
                    # Act / Assert
                    with self.assertRaises(ConflictError):
                        read_owned_state("ns", "deployment")


@unittest.skipUnless(
    os.getenv("POD_STATE_TEST_NAMESPACE") and os.getenv("POD_STATE_TEST_DEPLOYMENT"),
    "Set a real test namespace and deployment for Kubernetes integration",
)
class LivePodStateTests(unittest.TestCase):
    def test_owned_state_matches_live_controller_uids(self):
        # Arrange: use a deployment in a disposable test environment.
        from kubernetes import client

        from app.services.kubernetes_client import api_client

        namespace = os.environ["POD_STATE_TEST_NAMESPACE"]
        name = os.environ["POD_STATE_TEST_DEPLOYMENT"]
        # Act
        summary, pods = read_owned_state(namespace, name)
        # Assert: independently follow each returned pod's actual controller UIDs.
        with api_client() as api:
            apps, core = client.AppsV1Api(api), client.CoreV1Api(api)
            deployment = apps.read_namespaced_deployment(name, namespace)
            self.assertEqual(summary.uid, deployment.metadata.uid)
            self.assertTrue(pods, "Test fixture must have at least one owned pod")
            for observed in pods:
                pod = core.read_namespaced_pod(observed.name, namespace)
                self.assertEqual(observed.uid, pod.metadata.uid)
                controller = next(ref for ref in pod.metadata.owner_references if ref.controller)
                self.assertEqual(controller.kind, "ReplicaSet")
                rs = apps.read_namespaced_replica_set(controller.name, namespace)
                self.assertEqual(rs.metadata.uid, controller.uid)
                parent = next(ref for ref in rs.metadata.owner_references if ref.controller)
                self.assertEqual((parent.kind, parent.uid), ("Deployment", summary.uid))
