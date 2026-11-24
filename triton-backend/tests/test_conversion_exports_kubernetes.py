"""Isolated CI flow with real versioned object storage and Kubernetes claims."""

import base64
import hashlib
import os
import subprocess
from pathlib import Path
import time
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import boto3
from kubernetes import client  # type: ignore[import-untyped]

from app.db.entities import S3ProfileEntity
from app.schemas.task_workspaces import OpenTaskWorkspace
from app.services import conversion_exports as exports
from app.services import task_workspaces
from app.services.development import claim_retention, workspaces
from app.services.kubernetes_client import api_client
from tests.test_handoffs import HandoffFixture


@unittest.skipUnless(os.getenv("CONVERSION_EXPORT_TEST_S3"), "Requires isolated Kubernetes and versioned S3")
class ConversionExportIntegration(HandoffFixture, unittest.TestCase):
    def test_export_replace_restore_and_reclaim(self):
        ns = "conversion-test-" + uuid4().hex[:12]
        api = api_client()
        core, apps = client.CoreV1Api(api), client.AppsV1Api(api)
        core.create_namespace({"metadata": {"name": ns}})
        self.addCleanup(core.delete_namespace, ns)
        # Only the admission policy is applied. The chart needs an app Secret reference to render.
        chart = Path(__file__).resolve().parents[2] / "charts/triton-control"
        manifest = subprocess.check_output(["helm", "template", "retention-test", str(chart), "-n", ns,
            "--set", "development.workspaceRetention.enabled=true",
            "--set-string", "app.existingSecret=retention-test-app", "--show-only",
            "templates/workspace-claim-admission.yaml"])
        subprocess.run(["kubectl", "apply", "-f", "-"], input=manifest, check=True)
        self.addCleanup(subprocess.run, ["kubectl", "delete", "-f", "-"], input=manifest, check=True)
        profile = S3ProfileEntity(id=2, owner_user_id=1, name="exports", endpoint=os.environ["CONVERSION_EXPORT_TEST_S3"],
                                  bucket=ns, prefix="exports", access_key="conversion-test",
                                  secret_key_hash="fixture", secret_key_enc="conversion-test-secret")
        self.session.add(profile)
        self.session.commit()
        s3 = boto3.client("s3", endpoint_url=profile.endpoint, aws_access_key_id=profile.access_key,
                          aws_secret_access_key=profile.secret_key_enc, region_name="us-east-1")
        s3.create_bucket(Bucket=ns)
        s3.put_bucket_versioning(Bucket=ns, VersioningConfiguration={"Status": "Enabled"})
        self.enterContext(patch.object(workspaces, "workspace_namespace", return_value=ns))
        self.enterContext(patch.object(workspaces, "require_kubernetes_enabled"))
        self.enterContext(patch.object(task_workspaces, "apply_registry_settings", side_effect=lambda req, _: req))
        self.enterContext(patch.object(task_workspaces.k8s, "apply_code_server_resources", return_value=["fixture"]))
        self.enterContext(patch.object(task_workspaces.k8s, "delete_code_server_resources", return_value="retired"))
        receipt = self.submit(self.request(dataset={"profile_id": 1, "path": "dataset.json"},
                                           baseline={"profile_id": 1, "path": "baseline.json"},
                                           expected_behavior="Predict", acceptance_criteria="Matches baseline"))
        task = self.claim(receipt.task)
        def open_workspace():
            result = task_workspaces.open_workspace(self.session, self.engineer, task.id, OpenTaskWorkspace())
            name = result.workspace.name
            core.create_namespaced_secret(ns, {"metadata": {"name": name + "-secret"},
                "data": {"S3_PROFILE_TOKEN": base64.b64encode(b"test-token").decode()}})
            core.create_namespaced_persistent_volume_claim(ns, {
                "metadata": {"name": "workspace-" + name + "-0", "annotations": {
                    "triton-control.io/workspace-name": name, "triton-control.io/task-id": str(task.id),
                    "triton-control.io/installation-id": "test-install"}},
                "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "64Mi"}}}})
            return result.workspace
        original = open_workspace()
        name = original.name
        claim_name = "workspace-" + name + "-0"
        def write(command):
            pod = name + "-0"
            core.create_namespaced_pod(ns, {"metadata": {"name": pod}, "spec": {
                "restartPolicy": "Never", "containers": [{"name": "writer", "image": "python:3.12-alpine",
                "command": ["sh", "-ec", command], "volumeMounts": [{"mountPath": "/data", "name": "data"}]}],
                "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim_name}}]}})
            deadline = time.monotonic() + 120
            while core.read_namespaced_pod(pod, ns).status.phase != "Succeeded":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(1)
            core.delete_namespaced_pod(pod, ns, grace_period_seconds=0)
            while any(item.metadata.name == pod for item in core.list_namespaced_pod(ns).items):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(1)
        write("mkdir -p /data/conversion/iris/1; printf config > /data/conversion/iris/config.pbtxt; "
              "printf weights > /data/conversion/iris/1/model")
        export_id = uuid4()
        prefix = f"{profile.prefix}/task-conversions/{task.id}/{export_id}/iris/"
        files = []
        for path, data in [("config.pbtxt", b"config"), ("1/model", b"weights")]:
            key = prefix + path
            version = s3.put_object(Bucket=ns, Key=key, Body=data)["VersionId"]
            files.append(exports.ExportObject(key=key, size=len(data), version_id=version,
                                              sha256=hashlib.sha256(data).hexdigest()))
        request = exports.ConversionExport(export_id=export_id, profile_id=2, directory="iris", objects=files)
        exports.export_event(self.session, ns, name, "test-token", request, "conversion_export_started")
        exports.export_event(self.session, ns, name, "test-token", request, "conversion_export_interrupted")
        self.assertFalse(exports.saved_conversions(self.session, ns, name, "test-token"))
        exports.record(self.session, ns, name, "test-token", request)
        exports.record(self.session, ns, name, "test-token", request)
        workspaces.delete_code_server(self.session, self.engineer, original.id)
        replacement = open_workspace()
        saved = exports.saved_conversions(self.session, ns, replacement.name, "test-token")
        self.assertEqual(len(saved), 1)
        result = exports.saved_receipt(self.session, ns, replacement.name, "test-token", export_id)
        for obj in result["objects"]:
            s3.put_object(Bucket=ns, Key=obj["key"], Body=b"new version")
            body = s3.get_object(Bucket=ns, Key=obj["key"], VersionId=obj["version_id"])["Body"]
            self.assertEqual(hashlib.sha256(body.read()).hexdigest(), obj["sha256"])
            body.close()
        def reclaim():
            claim = core.read_namespaced_persistent_volume_claim(claim_name, ns)
            return claim_retention.evaluate(self.session, core, apps, ns, claim, "test-install", timedelta(), True)
        write("printf changed > /data/conversion/iris/1/model")
        self.assertIn("changed", reclaim())
        write("printf weights > /data/conversion/iris/1/model; touch /data/unsaved")
        self.assertIn("unexported", reclaim())
        write("rm /data/unsaved")
        original_lock = claim_retention.preparation.get
        attempted = []
        def lock_during_audit(*args, **kwargs):
            if kwargs.get("lock"):
                for writer_name, expected_status in [("short-lived-writer", 403), (name + "-0", 409)]:
                    writer = {"metadata": {"name": writer_name}, "spec": {"containers": [{"name": "writer",
                        "image": "python:3.12-alpine", "volumeMounts": [{"name": "data", "mountPath": "/data"}]}],
                        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim_name}}]}}
                    with self.assertRaises(client.ApiException) as denied:
                        core.create_namespaced_pod(ns, writer)
                    self.assertEqual(denied.exception.status, expected_status)
                    attempted.append(writer_name)
            return original_lock(*args, **kwargs)
        with patch.object(claim_retention.preparation, "get", side_effect=lock_during_audit):
            self.assertIn("Deletion requested", reclaim())
        self.assertEqual(attempted, ["short-lived-writer", name + "-0"])
        deadline = time.monotonic() + 120
        while any(c.metadata.name == claim_name for c in core.list_namespaced_persistent_volume_claim(ns).items):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(1)
        for _ in range(2):
            remaining = core.list_namespaced_persistent_volume_claim(ns).items
            self.assertEqual([c.metadata.name for c in remaining], ["workspace-" + replacement.name + "-0"])
            self.assertTrue(claim_retention.evaluate(self.session, core, apps, ns, remaining[0], "test-install",
                                                     timedelta(), True).startswith("Retained"))
            claim_retention.report_volumes(self.session, core, ns, "test-install", True)
