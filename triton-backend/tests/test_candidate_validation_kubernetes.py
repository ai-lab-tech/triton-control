"""Drive the real Job lifecycle against a pinned, synthetic Triton protocol runtime."""

import base64
import hashlib
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from kubernetes import client  # type: ignore[import-untyped]

from app.core.canonical import canonical_bytes, inventory_digest
from app.db.entities import OperationEntity
from app.schemas.materialization import StorageBinding
from app.schemas.validation import ValidationManifest
from app.services.candidate_validation.execution import reconcile
from app.services.candidate_validation.workload import Inputs
from tests import test_materialization_kubernetes as cluster
from tests.materialization_fixtures import plan, policy
from tests.test_candidate_validation import fixture

RUNTIME = os.getenv("VALIDATION_TEST_RUNTIME", "")


@unittest.skipUnless(RUNTIME and cluster.IMAGE and cluster.KUBECONFIG, "Requires the disposable qualification cluster")
class ValidationJobTests(cluster.MaterializerKubernetesFixture):
    def run_case(self, expected, *, supported=True):
        suffix = uuid4().hex[:8]
        claim = "inputs-" + suffix
        seed = "seed-" + suffix
        desired = plan(cluster.IMAGE)
        artifacts = {}
        raw = fixture("manifest")

        def put(name, data):
            key = hashlib.sha256(data).hexdigest()
            artifacts[key] = base64.b64encode(data).decode()
            return {"uri": name, "digest": "sha256:" + key}

        raw.update(
            image=RUNTIME,
            sourceCode=None,
            dataset=put("dataset", b"3"),
            expectedOutputs=put("expected", json.dumps(expected).encode()),
        )
        raw["evaluator"].update(
            image=RUNTIME,
            code=put(
                "evaluate.py", Path(__file__).with_name("validation_runtime").joinpath("evaluator.py").read_bytes()
            ),
        )
        raw["repository"].update(
            uri="s3://fixture/validation",
            materializerImage=cluster.IMAGE,
            inventoryDigest=inventory_digest(desired.source_inventory),
            preparedInventoryDigest=inventory_digest(desired.prepared_inventory),
        )
        raw["startup"]["models"] = [{"name": "iris", "versions": ["1"]}]
        startup = {key: raw["startup"][key] for key in ("models", "resourcesPerReplica")}
        raw["startup"]["configuration"] = put("startup.json", canonical_bytes(startup))
        cpu = next(
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith(("model name", "CPU part"))
        )
        raw["supportedTargets"][0].update(
            architecture=cluster.PLATFORM.split("/")[1],
            cpuModel=hashlib.sha256(cpu.encode()).hexdigest() if supported else "another-cpu",
            tritonVersion="fixture-1",
        )
        raw["criteria"][1].update(metric="latency", threshold="10000")
        manifest = ValidationManifest.model_validate(raw)
        self.core.create_namespaced_persistent_volume_claim(
            self.ns,
            {
                "metadata": {"name": claim},
                "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "8Mi"}}},
            },
        )
        self.core.create_namespaced_pod(
            self.ns,
            {
                "metadata": {"name": seed},
                "spec": {
                    "restartPolicy": "Never",
                    "securityContext": {"fsGroup": 10001},
                    "containers": [
                        {
                            "name": "seed",
                            "image": cluster.IMAGE,
                            "command": [
                                "python",
                                "-c",
                                "import sys,json,base64,pathlib\nfor k,v in json.loads(sys.argv[1]).items(): "
                                "(pathlib.Path('/data')/k).write_bytes(base64.b64decode(v))",
                                json.dumps(artifacts),
                            ],
                            "volumeMounts": [{"name": "inputs", "mountPath": "/data"}],
                        }
                    ],
                    "volumes": [{"name": "inputs", "persistentVolumeClaim": {"claimName": claim}}],
                },
            },
        )
        for _ in range(90):
            phase = self.core.read_namespaced_pod(seed, self.ns).status.phase
            if phase == "Succeeded":
                break
            self.assertNotEqual(phase, "Failed")
            time.sleep(1)
        self.assertEqual(phase, "Succeeded")
        task = uuid4()
        inputs = Inputs(
            evaluator=manifest.evaluator,
            claim=claim,
            plan=desired,
            policy=policy(cluster.IMAGE, cluster.PLATFORM),
            storage=StorageBinding(
                bucket="fixture",
                prefix="validation",
                secret_name="fixture",
                endpoint="http://fixture:8080",
                allow_http=True,
            ),
        )
        self.core.create_namespaced_config_map(
            self.ns,
            {
                "metadata": {"name": "validation-inputs-" + task.hex},
                "immutable": True,
                "data": {"inputs.json": inputs.model_dump_json()},
            },
        )
        operation = OperationEntity(id=uuid4().hex, target=str(task), payload=raw, result={})
        batch = client.BatchV1Api(self.api)
        with patch.dict(os.environ, TRITON_VALIDATION_NAMESPACE=self.ns):
            for _ in range(150):
                outcome = reconcile(operation, Mock(), self.core, batch)
                operation.result = outcome.result
                if outcome.state != "pending":
                    break
                time.sleep(1)
        self.assertEqual(batch.list_namespaced_job(self.ns).items, [])
        return outcome

    def test_matching_prediction_passes_correctness_and_latency(self):
        outcome = self.run_case(6)
        self.assertEqual(outcome.state, "succeeded", outcome.message)
        self.assertEqual(outcome.result["report"]["status"], "passed")

    def test_wrong_prediction_retains_failed_report(self):
        outcome = self.run_case(7)
        self.assertEqual(outcome.state, "failed", outcome.message)
        self.assertEqual(outcome.result["report"]["status"], "failed")
        self.assertEqual(outcome.result["report"]["metrics"][0]["value"], "0")

    def test_unsupported_measured_target_is_retained_without_a_pass(self):
        outcome = self.run_case(6, supported=False)
        self.assertEqual(outcome.state, "failed", outcome.message)
        self.assertEqual(outcome.result["report"]["status"], "unsupported")
