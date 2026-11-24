import json
import shlex
import unittest
from copy import deepcopy

from app.schemas.serving_metadata import StartupModel
from app.services.deployment.replica_startup import serving_template


class ReplicaStartupTests(unittest.TestCase):
    def test_native_s3_scaling_preserves_image_launch_setup_credentials_and_resources(self):
        # Arrange
        setup = "export HOME=/tmp\nexec tritonserver "
        repository = "--model-repository=s3://http://storage:8333/models/project"
        original = {"spec": {"containers": [{
            "name": "triton", "image": "team/serving:custom", "command": ["/bin/bash", "-c"],
            "args": [setup + repository + " --model-control-mode=explicit --load-model='*'"],
            "envFrom": [{"secretRef": {"name": "storage"}}], "resources": {"limits": {"nvidia.com/gpu": "1"}},
            "readinessProbe": {"httpGet": {"path": "/v2/health/ready", "port": 8000}, "periodSeconds": 3},
        }]}}
        before = deepcopy(original)
        models = (StartupModel(name="iris", versions=("1", "2")),)

        # Act
        updated = serving_template(original, models)

        # Assert
        self.assertEqual(original, before)
        self.assertEqual(set(updated["spec"]), {"containers"})
        self.assertEqual(len(updated["spec"]["containers"]), 1)
        runtime = updated["spec"]["containers"][0]
        self.assertTrue(runtime["args"][0].startswith(setup))
        self.assertEqual(shlex.split(runtime["args"][0][len(setup):]),
                         [repository, "--model-control-mode=explicit", "--load-model=iris"])
        probe = runtime["readinessProbe"]
        self.assertNotIn("httpGet", probe)
        self.assertEqual(probe["periodSeconds"], 3)
        self.assertEqual(json.loads(probe["exec"]["command"][-1]), [["iris", "1"], ["iris", "2"]])
        for key in ("image", "command", "envFrom", "resources"):
            self.assertEqual(runtime[key], original["spec"]["containers"][0][key])
