import io
import json
import os
import unittest
from dataclasses import asdict
from unittest.mock import patch

from app.db.entities import TritonInstanceEntity
from app.exceptions import ConflictError
from app.schemas.pods import PodState
from app.schemas.serving_metadata import StartupModel
from app.services.deployment import scale_snapshot
from tests.materialization_fixtures import IMAGE, LIMITS, policy


class ScaleSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.instance = TritonInstanceEntity(s3_bucket="models", deployment_secret_name="storage")
        self.raw = {
            "metadata": {"namespace": "test", "uid": "uid"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "triton",
                                "image": IMAGE,
                                "command": ["/bin/bash", "-c"],
                                "args": [
                                    "exec tritonserver --model-repository=s3://models --model-control-mode=explicit"
                                ],
                            }
                        ]
                    }
                }
            },
        }
        self.pod = PodState(uid="uid", name="pod", phase="Running", ready=True, restarts=0, terminating=False)
        self.models = (StartupModel(name="iris", versions=("1",)),)
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    scale_snapshot.POLICY_ENV: json.dumps(
                        {
                            "policy": policy().model_dump(),
                            "limits": asdict(LIMITS),
                        }
                    )
                },
            )
        )
        self.client = self.enterContext(patch.object(scale_snapshot, "build_s3_client")).return_value
        self.client.list_objects_v2.return_value = {}
        self.client.get_paginator.return_value.paginate.return_value = [
            {
                "Contents": [
                    {"Key": key, "ETag": "etag", "Size": 1}
                    for key in (
                        "iris/1/model.onnx",
                        "iris/2/model.onnx",
                        "iris/config.pbtxt",
                        "other/1/model.onnx",
                    )
                ]
            }
        ]
        self.client.head_object.return_value = {"ETag": "etag", "VersionId": "v1", "ContentLength": 1}
        self.client.get_object.side_effect = lambda **_: {"Body": io.BytesIO(b"x")}

    def test_scale_pins_only_ready_versions_and_their_shared_configuration(self):
        # Arrange
        # Act
        release, manifest, pod = scale_snapshot.render(self.instance, self.raw, self.pod, self.models)

        # Assert
        plan = json.loads(manifest["data"]["plan.json"])
        self.assertEqual(
            plan["objects"],
            [
                {"path": "iris/1/model.onnx", "version_id": "v1"},
                {"path": "iris/config.pbtxt", "version_id": "v1"},
            ],
        )
        self.assertEqual(json.loads(manifest["data"]["readiness.json"])["release"], release)
        self.assertEqual(pod["spec"]["containers"][0]["image"], IMAGE)

    def test_missing_version_id_blocks_scale(self):
        # Arrange
        self.client.head_object.return_value["VersionId"] = "null"

        # Act / Assert
        with self.assertRaisesRegex(ConflictError, "versioning"):
            scale_snapshot.render(self.instance, self.raw, self.pod, self.models)
