"""Real Kubernetes init failure/restart and read-only volume enforcement.

Uses only a unique test namespace and a tiny HTTP fixture, not a serving deployment.
"""

import hashlib
import os
import shlex
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from kubernetes import client, utils  # type: ignore[import-untyped]

from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services.development import kubernetes as workspace_k8s
from app.services.development.task_staging import configure
from app.services.kubernetes_client import api_client


@unittest.skipUnless(os.getenv("TASK_WORKSPACE_TEST_KUBERNETES") == "1", "Opt-in Kubernetes test")
class TaskWorkspaceKubernetesTests(unittest.TestCase):
    def setUp(self):
        self.namespace = "tc-task-workspace-test-" + uuid4().hex[:10]
        self.api = api_client()
        self.core = client.CoreV1Api(self.api)
        self.core.create_namespace({"metadata": {"name": self.namespace}})
        self.addCleanup(self.core.delete_namespace, self.namespace)
        self.addCleanup(self.api.close)
        server = """
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer fixture-only':
            self.send_response(403); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(b'model')
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
"""
        self.core.create_namespaced_pod(self.namespace, {
            "metadata": {"name": "source", "labels": {"app": "source"}},
            "spec": {"containers": [{"name": "source", "image": "python:3.12-alpine",
                                     "command": ["python3", "-c", server]}]},
        })
        self.core.create_namespaced_service(self.namespace, {
            "metadata": {"name": "source"},
            "spec": {"selector": {"app": "source"}, "ports": [{"port": 8080}]},
        })
        self.wait("source", lambda pod: pod.status.phase == "Running")

    def wait(self, name, predicate):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            pod = self.core.read_namespaced_pod(name, self.namespace)
            if predicate(pod):
                return pod
            time.sleep(1)
        self.fail(f"Timed out waiting for {name}: {pod.status.phase}")

    def pod(self, name, corrupt=False):
        request = OpenTaskWorkspace(workspace={"image": "python:3.12-alpine"}).workspace
        manifests = workspace_k8s._manifests(request, self.namespace, name, name + "-svc", name + "-secret")
        document = {"files": [{"path": "source/model.bin", "size": 5,
                    "digest": "sha256:" + hashlib.sha256(b"wrong" if corrupt else b"model").hexdigest()}]}
        with patch("app.services.development.task_staging.control_url",
                   return_value=f"http://source.{self.namespace}.svc.cluster.local:8080"):
            configure(manifests, document, self.namespace, name, name + "-secret")
        configmap = manifests[0]
        utils.create_from_dict(self.api, configmap, namespace=self.namespace)
        self.core.create_namespaced_secret(self.namespace, {
            "metadata": {"name": name + "-secret"}, "stringData": {"S3_PROFILE_TOKEN": "fixture-only"},
        })
        spec = next(m for m in manifests if m["kind"] == "StatefulSet")["spec"]["template"]["spec"]
        main = spec["containers"][0]
        # Keep the generated staging/isolation wiring; replace the editor with a
        # filesystem probe so this test does not install code-server/extensions.
        startup = main["args"][0].split("CODE_SERVER_RUNTIME=", 1)[0]
        main["command"] = ["/bin/sh", "-ec"]
        probe = """
from pathlib import Path
import errno, json, time
p = Path('/inputs/verified/source/model.bin')
assert p.read_bytes() == b'model'
try:
    p.write_bytes(b'changed')
except OSError as exc:
    assert exc.errno == errno.EROFS, exc
else:
    raise RuntimeError('input mount is writable')
folders = json.loads(Path('/workspace/.triton-control/workspace.code-workspace').read_text())['folders']
assert folders[0]['path'] == '/workspace/conversion'
assert folders[1]['path'] == '/inputs/verified'
Path(folders[0]['path'], 'output').write_bytes(b'converted')
print('READ_ONLY_INPUTS_AND_WRITABLE_CONVERSION_OK', flush=True)
time.sleep(300)
"""
        main["args"] = [startup + "exec python3 -c " + shlex.quote(probe)]
        main.pop("startupProbe", None)
        main.pop("readinessProbe", None)
        main["env"] = []
        main["volumeMounts"] = [m for m in main["volumeMounts"] if m["name"] in {"workspace", "task-inputs"}]
        spec["volumes"] = [v for v in spec["volumes"] if v["name"] in {"task-inputs", "task-stager"}]
        spec["volumes"].append({"name": "workspace", "emptyDir": {}})
        spec["restartPolicy"] = "Never"
        return {"metadata": {"name": name}, "spec": spec}

    def test_successful_staging_is_readonly_after_pod_recreation(self):
        pod = self.pod("verified")
        for attempt in range(2):
            self.core.create_namespaced_pod(self.namespace, pod)
            self.wait("verified", lambda item: item.status.phase == "Running")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                logs = self.core.read_namespaced_pod_log("verified", self.namespace, container="code-server")
                if "READ_ONLY_INPUTS_AND_WRITABLE_CONVERSION_OK" in logs:
                    break
                time.sleep(1)
            self.assertIn("READ_ONLY_INPUTS_AND_WRITABLE_CONVERSION_OK", logs)
            if attempt == 0:
                self.core.delete_namespaced_pod("verified", self.namespace, grace_period_seconds=0)
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        self.core.read_namespaced_pod("verified", self.namespace)
                    except client.exceptions.ApiException as exc:
                        if exc.status == 404:
                            break
                        raise
                    time.sleep(1)

    def test_corrupt_input_prevents_editor_start(self):
        self.core.create_namespaced_pod(self.namespace, self.pod("corrupt", corrupt=True))
        pod = self.wait("corrupt", lambda item: item.status.phase == "Failed")
        statuses = pod.status.init_container_statuses
        self.assertNotEqual(statuses[0].state.terminated.exit_code, 0)
        self.assertFalse(any(item.ready for item in pod.status.container_statuses or []))
        logs = self.core.read_namespaced_pod_log("corrupt", self.namespace, container="stage-task-inputs")
        self.assertIn("Task input staging failed", logs)
        self.assertNotIn("fixture-only", logs)
