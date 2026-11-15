"""Ownership, rollout evidence, bounded log reads and HTTP authorization contracts."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]

from app.api.pod_api import router
from app.core.security import get_claims
from app.db.database import get_session
from app.exceptions import ForbiddenError, NotFoundError
from app.services.deployment import pod_provider as provider
from app.services.deployment import pods


def owner(kind, uid, name="owner"):
    return {"kind": kind, "uid": uid, "name": name, "controller": True}


def fixtures():
    dep = {
        "metadata": {"name": "serving", "uid": "dep-1", "generation": 3, "annotations": {provider.REVISION_KEY: "2"}},
        "spec": {"replicas": 3, "template": {"metadata": {"annotations": {provider.RELEASE_KEY: "r2"}}}},
        "status": {"observedGeneration": 3, "updatedReplicas": 2, "readyReplicas": 1, "availableReplicas": 1},
    }
    rs = {
        "metadata": {
            "uid": "rs-1",
            "name": "owner",
            "ownerReferences": [owner("Deployment", "dep-1")],
            "annotations": {provider.REVISION_KEY: "2"},
        }
    }
    pod = {
        "metadata": {
            "name": "pod-a",
            "uid": "pod-1",
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "ownerReferences": [owner("ReplicaSet", "rs-1")],
            "annotations": {provider.RELEASE_KEY: "r2"},
        },
        "spec": {
            "containers": [{"name": "triton", "image": "triton:test"}],
            "initContainers": [
                {"name": "download", "image": "sync:test"},
                {"name": "sync", "image": "sync:test", "restartPolicy": "Always"},
            ],
        },
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "False"}],
            "containerStatuses": [
                {
                    "name": "triton",
                    "ready": False,
                    "restartCount": 2,
                    "state": {"waiting": {"reason": "CrashLoopBackOff", "message": "crashed"}},
                    "lastState": {"terminated": {"reason": "OOMKilled"}},
                }
            ],
        },
    }
    return dep, rs, pod


class PodTests(unittest.TestCase):
    def setUp(self):
        self.dep, self.rs, self.pod = fixtures()
        self.apps, self.core = Mock(), Mock()
        for name, value in [("AppsV1Api", self.apps), ("CoreV1Api", self.core)]:
            p = patch.object(provider.client, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.reader = provider.PodProvider(client.ApiClient())
        self.apps.read_namespaced_deployment.return_value = self.dep
        self.apps.list_namespaced_replica_set.return_value = {"items": [self.rs]}
        self.apps.read_namespaced_replica_set.return_value = self.rs
        self.core.list_namespaced_pod.return_value = {"items": [self.pod]}
        self.core.read_namespaced_pod.return_value = self.pod

    def test_counts_and_running_not_ready_with_init_and_sidecar(self):
        dep, items = self.reader.snapshot("ns", "serving")
        self.assertEqual((dep.desired, dep.updated, dep.ready, dep.available), (3, 2, 1, 1))
        self.assertEqual(items[0].phase, "Running")
        self.assertFalse(items[0].ready)
        self.assertEqual(items[0].reason, "CrashLoopBackOff")
        self.assertEqual(items[0].restarts, 2)
        self.assertEqual([c.kind for c in items[0].containers], ["init", "sidecar", "regular"])
        self.assertEqual(items[0].release, "r2")
        self.assertTrue(items[0].current_revision)

    def test_rejects_unrelated_pod_uids_despite_identical_labels(self):
        # Arrange
        stranger = copy.deepcopy(self.pod)
        stranger["metadata"]["uid"] = "stranger"
        stranger["metadata"]["ownerReferences"][0]["uid"] = "unrelated"
        self.core.list_namespaced_pod.return_value = {"items": [stranger, self.pod]}
        # Act
        _, items = self.reader.snapshot("ns", "serving")
        # Assert
        self.assertEqual([p.uid for p in items], ["pod-1"])

    def test_rejects_replicaset_owned_by_old_deployment(self):
        # Arrange
        self.rs["metadata"]["ownerReferences"][0]["uid"] = "old-deployment"
        # Act
        _, items = self.reader.snapshot("ns", "serving")
        # Assert
        self.assertEqual(items, [])

    def test_controller_reference_required(self):
        self.rs["metadata"]["ownerReferences"][0]["controller"] = False
        self.assertEqual(self.reader.snapshot("ns", "serving")[1], [])

    def test_stale_generation_and_termination(self):
        self.dep["status"]["observedGeneration"] = 2
        self.pod["metadata"]["deletionTimestamp"] = "2026-01-01T01:00:00Z"
        self.pod["status"]["conditions"][0]["status"] = "True"
        dep, items = self.reader.snapshot("ns", "serving")
        self.assertTrue(dep.stale)
        self.assertTrue(items[0].terminating)
        self.assertFalse(items[0].ready)

    def test_older_revision_and_missing_release_are_not_invented(self):
        self.rs["metadata"]["annotations"][provider.REVISION_KEY] = "1"
        self.pod["metadata"].pop("annotations")
        p = self.reader.snapshot("ns", "serving")[1][0]
        self.assertFalse(p.current_revision)
        self.assertIsNone(p.release)

    def test_gpu_shortage_exposes_scheduling_reason(self):
        # Arrange
        self.pod["status"].pop("containerStatuses")
        self.pod["status"]["conditions"] = [
            {
                "type": "PodScheduled",
                "status": "False",
                "reason": "Unschedulable",
                "message": "Insufficient nvidia.com/gpu",
            }
        ]
        # Act
        _, items = self.reader.snapshot("ns", "serving")
        # Assert
        self.assertEqual(items[0].reason, "Unschedulable")
        self.assertIn("nvidia.com/gpu", items[0].message)

    def test_init_image_pull_failure_exposes_reason(self):
        for reason in ["ImagePullBackOff", "ErrImagePull"]:
            with self.subTest(reason=reason):
                # Arrange
                self.pod["status"].pop("containerStatuses", None)
                self.pod["status"]["initContainerStatuses"] = [
                    {"name": "download", "state": {"waiting": {"reason": reason}}}
                ]
                # Act
                _, items = self.reader.snapshot("ns", "serving")
                # Assert
                self.assertEqual(items[0].reason, reason)

    def test_init_nonzero_exit_exposes_failure(self):
        # Arrange
        self.pod["status"].pop("containerStatuses")
        self.pod["status"]["initContainerStatuses"] = [
            {"name": "download", "state": {"terminated": {"reason": "Error", "exitCode": 1}}}
        ]
        # Act
        _, items = self.reader.snapshot("ns", "serving")
        # Assert
        self.assertEqual(items[0].reason, "Error")

    def test_deployment_changed_during_observation(self):
        newer = copy.deepcopy(self.dep)
        newer["metadata"]["generation"] = 4
        self.apps.read_namespaced_deployment.side_effect = [self.dep, newer]
        self.assertTrue(self.reader.snapshot("ns", "serving")[0].stale)

    def test_paginated_resource_list(self):
        self.core.list_namespaced_pod.side_effect = [
            {"items": [], "metadata": {"continue": "next"}},
            {"items": [self.pod]},
        ]
        self.assertEqual(len(self.reader.snapshot("ns", "serving")[1]), 1)
        self.assertEqual(self.core.list_namespaced_pod.call_args.kwargs["_continue"], "next")

    def test_log_read_preserves_options_and_bounds_utf8(self):
        pod = self.reader.snapshot("ns", "serving")[1][0]
        response = Mock()
        response.read.return_value = "2026-01-01T00:00:00Z hello é".encode()
        self.core.read_namespaced_pod_log.return_value = response
        text, truncated = self.reader.logs("ns", "serving", "dep-1", pod, "download", True, None, 10, 27)
        self.assertLessEqual(len(text.encode()), 27)
        kwargs = self.core.read_namespaced_pod_log.call_args.kwargs
        self.assertTrue(kwargs["previous"])
        self.assertTrue(kwargs["timestamps"])
        self.assertEqual(kwargs["container"], "download")
        self.assertEqual(kwargs["tail_lines"], 10)
        response.close.assert_called_once()
        response.release_conn.assert_called_once()

    def test_same_name_replacement_discards_log_bytes(self):
        pod = self.reader.snapshot("ns", "serving")[1][0]
        replacement = copy.deepcopy(self.pod)
        replacement["metadata"]["uid"] = "replacement"
        self.core.read_namespaced_pod.side_effect = [self.pod, replacement]
        response = Mock()
        response.read.return_value = b"must not escape"
        self.core.read_namespaced_pod_log.return_value = response
        with self.assertRaises(NotFoundError):
            self.reader.logs("ns", "serving", "dep-1", pod, "triton", False, None, 10, 1024)

    def test_changed_replicaset_uid_cannot_read_logs(self):
        pod = self.reader.snapshot("ns", "serving")[1][0]
        self.rs["metadata"]["uid"] = "replacement-rs"
        with self.assertRaises(NotFoundError):
            self.reader.logs("ns", "serving", "dep-1", pod, "triton", False, None, 10, 1024)
        self.core.read_namespaced_pod_log.assert_not_called()


class PodApiTests(unittest.TestCase):
    def setUp(self):
        pods._log_reads.clear()
        instance = SimpleNamespace(
            is_self_deployed=True,
            deployment_runtime="kubernetes",
            deployment_namespace="ns",
            deployment_name="serving",
            health_live=True,
            health_ready=False,
            health_last_checked_at=None,
        )
        self.instance = instance
        self.access = patch.object(pods, "get_instance_or_404", return_value=instance).start()
        self.addCleanup(patch.stopall)
        dep, rs, pod = fixtures()
        self.pod = provider.pod_state(pod, rs, "2")
        self.dep = SimpleNamespace(uid="dep-1")
        self.provider = Mock()
        self.provider.snapshot.return_value = self.dep, [self.pod]
        self.provider.logs.return_value = ("2026-01-01T00:00:00Z hello", False)
        patch.object(pods, "api_client", return_value=MagicMock()).start()
        patch.object(provider, "PodProvider", return_value=self.provider).start()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_claims] = lambda: {"sub": "alice"}
        app.dependency_overrides[get_session] = lambda: Mock()
        self.http = TestClient(app)

    def test_external_health_only(self):
        self.instance.is_self_deployed = False
        r = self.http.get("/api/instances/1/pods")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["deployment"])
        self.assertEqual(r.json()["pods"], [])
        self.assertTrue(r.json()["endpoint_live"])
        self.assertEqual(self.http.get("/api/instances/1/pod-logs").status_code, 400)
        self.provider.snapshot.assert_not_called()

    def test_non_kubernetes_runtime_does_not_read_pods_even_with_managed_flag(self):
        for runtime in ("external", "docker", ""):
            with self.subTest(runtime=runtime):
                self.instance.deployment_runtime = runtime
                r = self.http.get("/api/instances/1/pods")
                self.assertEqual(r.status_code, 200)
                self.assertFalse(r.json()["managed"])
                self.assertEqual(r.json()["pods"], [])
                self.assertEqual(self.http.get("/api/instances/1/pod-logs").status_code, 400)
        self.provider.snapshot.assert_not_called()
        self.provider.logs.assert_not_called()

    def test_access_denied_before_kubernetes_reads(self):
        self.access.side_effect = ForbiddenError("No instance access")
        for path in ["pods", "pod-logs"]:
            self.assertEqual(self.http.get(f"/api/instances/1/{path}").status_code, 403)
        self.provider.snapshot.assert_not_called()

    def test_missing_uid_is_explicit_not_replaced(self):
        r = self.http.get("/api/instances/1/pod-logs?pod_uid=removed")
        self.assertEqual(r.status_code, 404)
        self.provider.logs.assert_not_called()

    def test_invalid_container_is_rejected(self):
        r = self.http.get("/api/instances/1/pod-logs?pod_uid=pod-1&container=foreign")
        self.assertEqual(r.status_code, 404)
        self.provider.logs.assert_not_called()

    def test_all_containers_partial_failure_and_previous(self):
        self.provider.logs.side_effect = [
            ("2026-01-01T00:00:00Z init", False),
            ApiException(status=400),
            ("2026-01-01T00:00:01Z runtime", False),
        ]
        r = self.http.get("/api/instances/1/pod-logs?previous=true")
        self.assertEqual(r.status_code, 200)
        streams = r.json()["streams"]
        self.assertEqual([s["container"] for s in streams], ["download", "sync", "triton"])
        self.assertIn("unavailable", streams[1]["error"])
        self.assertTrue(all(s["previous"] and s["pod_uid"] == "pod-1" for s in streams))
        self.assertTrue(streams[2]["text"].endswith("runtime"))
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_refresh_limit(self):
        self.assertEqual(self.http.get("/api/instances/1/pod-logs").status_code, 200)
        self.assertEqual(self.http.get("/api/instances/1/pod-logs").status_code, 429)

    def test_line_byte_since_bounds(self):
        for query in [
            "lines=2001",
            "lines=0",
            "byte_limit=1048577",
            "byte_limit=0",
            "since=2026-01-01T00:00:00",
            "since=2999-01-01T00:00:00Z",
        ]:
            self.assertEqual(self.http.get("/api/instances/1/pod-logs?" + query).status_code, 422, query)
        self.provider.logs.assert_not_called()

    def test_total_bytes_and_stream_count_are_bounded(self):
        self.provider.logs.return_value = ("x" * 1024, True)
        r = self.http.get("/api/instances/1/pod-logs?byte_limit=1024")
        self.assertEqual(sum(len(s["text"].encode()) for s in r.json()["streams"]), 1024)
        self.assertTrue(r.json()["truncated"])
        self.assertEqual(self.provider.logs.call_count, 1)

    def test_kubernetes_failures_do_not_leak_details(self):
        self.provider.snapshot.side_effect = RuntimeError("credentials should stay private")
        r = self.http.get("/api/instances/1/pod-logs")
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("credentials", r.text)


class LegacyLogsTests(unittest.TestCase):
    def test_previous_errors_do_not_hide_current_logs_and_pod_failure(self):
        _, rs, raw = fixtures()
        raw["status"].update(phase="Failed", reason="UnexpectedAdmissionError", message="GPU rejected")
        pod = provider.pod_state(raw, rs, "2")
        pod.containers = [c for c in pod.containers if c.name == "triton"]
        with (
            patch.object(provider, "api_client", return_value=MagicMock()),
            patch.object(provider, "PodProvider") as cls,
        ):
            reader = cls.return_value
            reader.snapshot.return_value = SimpleNamespace(uid="dep-1"), [pod]
            reader.logs.side_effect = [ApiException(status=500), ("current output", False)]
            text = provider.read_legacy_logs("ns", "serving")
            self.assertIn("UnexpectedAdmissionError", text)
            self.assertIn("GPU rejected", text)
            self.assertIn("previous unavailable", text)
            self.assertIn("uid/pod-1", text)
            self.assertTrue(text.endswith("current output"))
