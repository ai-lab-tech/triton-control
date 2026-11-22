"""Run the actual digest-pinned helper as an init container on real Kubernetes.

A Python probe replaces Triton: these tests qualify materialization and the
read-only mount, not inference correctness or release approval.
"""

import copy
import json
import os
import time
import unittest
from pathlib import Path
from uuid import uuid4

from kubernetes import client, config  # type: ignore[import-untyped]

from app.core.canonical import inventory_digest
from app.schemas.materialization import StorageBinding
from app.schemas.serving_metadata import StartupModel
from app.services.materialization.kubernetes import manifests, serving_manifests
from tests.materialization_fixtures import FILES, LIMITS, plan, policy, serving_release

IMAGE = os.getenv("MATERIALIZER_TEST_IMAGE", "")
KUBECONFIG = os.getenv("MATERIALIZER_TEST_KUBECONFIG", "")
PLATFORM = os.getenv("MATERIALIZER_TEST_PLATFORM", "")


@unittest.skipUnless(IMAGE and KUBECONFIG and PLATFORM, "Requires pinned helper, explicit kubeconfig and platform")
class MaterializerKubernetesFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config.load_kube_config(config_file=KUBECONFIG)
        cls.api = client.ApiClient()
        cls.addClassCleanup(cls.api.close)
        cls.core = client.CoreV1Api(cls.api)
        cls.ns = "tc-materialize-" + uuid4().hex[:10]
        cls.core.create_namespace({"metadata": {"name": cls.ns}})
        cls.addClassCleanup(lambda: cls.core.delete_namespace(cls.ns))
        cls.core.create_namespaced_config_map(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "data": {
                    "server.py": Path(__file__).with_name("materializer_s3_server.py").read_text(),
                    "files.json": json.dumps({key: value.decode() for key, value in FILES.items()}),
                },
            },
        )
        cls.core.create_namespaced_secret(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "stringData": {
                    "AWS_ACCESS_KEY_ID": "synthetic-fixture",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-fixture",
                },
            },
        )
        cls.core.create_namespaced_service(
            cls.ns,
            {
                "metadata": {"name": "fixture"},
                "spec": {
                    "selector": {"test": "s3"},
                    "ports": [{"port": 8080, "targetPort": 8080}],
                },
            },
        )
        cls.core.create_namespaced_pod(
            cls.ns,
            {
                "metadata": {"name": "fixture", "labels": {"test": "s3"}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "s3",
                            "image": IMAGE,
                            "command": ["python", "/fixture/server.py"],
                            "volumeMounts": [{"name": "fixture", "mountPath": "/fixture", "readOnly": True}],
                            "readinessProbe": {"httpGet": {"path": "/health", "port": 8080}, "periodSeconds": 1},
                        }
                    ],
                    "volumes": [{"name": "fixture", "configMap": {"name": "fixture"}}],
                },
            },
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pod = cls.core.read_namespaced_pod("fixture", cls.ns)
            if pod.status.container_statuses and all(c.ready for c in pod.status.container_statuses):
                return
            time.sleep(1)
        raise AssertionError("S3 fixture did not start: " + str(pod.status))



class MaterializerKubernetesTests(MaterializerKubernetesFixture):
    def resources(self, case, *, gpu_count=0):
        desired = plan(IMAGE)
        if case == "normalization":
            payload = desired.model_dump(mode="json")
            payload["normalization"]["rewrites"][0]["relative"] = "does-not-exist"
            desired = type(desired).model_validate(payload)
        if case == "prepared-hash":
            payload = desired.model_dump(mode="json")
            payload["prepared_inventory"]["files"][0]["digest"] = "sha256:" + "0" * 64
            desired = type(desired).model_validate(payload)
        release = serving_release()
        repository = release.spec.repository.model_copy(
            update={
                "uri": f"s3://fixture/{case}",
                "materializer_image": IMAGE,
                "inventory_digest": inventory_digest(desired.source_inventory),
                "prepared_inventory_digest": inventory_digest(desired.prepared_inventory),
            }
        )
        release = release.model_copy(update={"spec": release.spec.model_copy(update={"repository": repository})})
        models = (StartupModel(name="iris", versions=("1", "2")),)
        release = release.model_copy(update={"spec": release.spec.model_copy(update={"models": models})})
        resources = release.spec.resources_per_replica.model_copy(update={"gpu_count": gpu_count})
        release = release.model_copy(
            update={"spec": release.spec.model_copy(update={"resources_per_replica": resources})}
        )
        return manifests(
            release,
            desired,
            policy(IMAGE, PLATFORM),
            StorageBinding(
                bucket="fixture", prefix=case, secret_name="fixture", endpoint="http://fixture:8080", allow_http=True
            ),
            name=case + "-" + uuid4().hex[:6],
            namespace=self.ns,
            limits=LIMITS,
        )

    def execute(self, case, *, valid=False, followup=None):
        desired = plan(IMAGE)
        cm, pod = self.resources(case)
        # These finite fixtures validate only the init container and read-only mounts.
        pod["spec"]["containers"].pop()
        for probe in ("startupProbe", "livenessProbe", "readinessProbe"):
            pod["spec"]["containers"][0].pop(probe, None)
        pod["spec"]["restartPolicy"] = "Never"
        pod["spec"]["terminationGracePeriodSeconds"] = 1
        main = pod["spec"]["containers"][0]
        main["image"] = IMAGE
        main["resources"] = {"requests": {"cpu": "10m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}}
        main["command"] = ["python", "-c"]
        main["args"] = [
            """
import errno, json, os
from pathlib import Path
assert Path('/models/iris/1/weights/model.bin').read_bytes() == b'synthetic model fixture'
assert json.loads(Path('/models/iris/1/model.json').read_text())['model'] == '/models/iris/1/weights'
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
try:
    Path('/models/unexpected').write_text('forbidden')
except OSError as error:
    assert error.errno == errno.EROFS, error
else:
    raise AssertionError('model repository was writable')
print(Path('/var/run/triton-materialization/receipt.json').read_text())
"""
        ]
        init = pod["spec"]["initContainers"][0]
        if followup in {"restart", "changed-helper", "retained-corruption"}:
            repeated = copy.deepcopy(init)
            repeated["name"] = "reverify-models"
            if followup == "changed-helper":
                # Run the actual image with a changed declared helper; retained receipts must reject it.
                other = "fixture.invalid/changed@sha256:" + "0" * 64
                changed = desired.model_dump(mode="json")
                changed["materializer_image"] = other
                cm["data"]["changed.json"] = json.dumps(changed)
                repeated["args"][repeated["args"].index("--plan") + 1] = "/plan/changed.json"
                repeated["args"][repeated["args"].index("--expected-image") + 1] = other
            else:
                # A correct retained-volume retry must not contact storage at all.
                repeated["args"][repeated["args"].index("--endpoint") + 1] = "http://127.0.0.1:1"
            if followup == "retained-corruption":
                mutate = copy.deepcopy(init)
                mutate.update(
                    name="corrupt-retained",
                    command=["python", "-c"],
                    args=[
                        "from pathlib import Path; p=Path('/materialized/ready/models/iris/1/weights/model.bin'); "
                        "p.chmod(0o644); p.write_bytes(b'corrupt retained bytes')"
                    ],
                )
                pod["spec"]["initContainers"].append(mutate)
            pod["spec"]["initContainers"].append(repeated)
        elif followup == "interrupted-recovery":
            interrupted = copy.deepcopy(init)
            args = list(init["args"])
            args[args.index("--prefix") + 1] = "interrupted"
            interrupted.update(
                name="interrupt-download",
                command=["python", "-c"],
                args=[
                    "import subprocess,sys; from pathlib import Path; "
                    "result=subprocess.run([sys.executable,'-m','app.services.materialization.cli']+"
                    + repr(args)
                    + "); assert result.returncode == 1; "
                    "root=Path('/materialized'); assert not (root/'ready').exists(); "
                    "stale=root/'.staging-crashed'; stale.mkdir(); (stale/'partial').write_bytes(b'partial')"
                ],
            )
            verify = copy.deepcopy(init)
            verify.update(
                name="verify-recovery",
                command=["python", "-c"],
                args=["from pathlib import Path; assert not list(Path('/materialized').glob('.staging-*'))"],
            )
            pod["spec"]["initContainers"] = [interrupted, init, verify]
        self.core.create_namespaced_config_map(self.ns, cm)
        self.core.create_namespaced_pod(self.ns, pod)
        name = pod["metadata"]["name"]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            actual = self.core.read_namespaced_pod(name, self.ns)
            if actual.status.phase in {"Succeeded", "Failed"}:
                break
            time.sleep(1)
        logs = self.core.read_namespaced_pod_log(name, self.ns, container=pod["spec"]["initContainers"][-1]["name"])
        if valid:
            self.assertEqual(actual.status.phase, "Succeeded", str(actual.status) + "\n" + logs)
            response = self.core.read_namespaced_pod_log(name, self.ns, container="triton", _preload_content=False)
            result = json.loads(response.data.decode())
            self.assertEqual(result["prepared_inventory_digest"], inventory_digest(desired.prepared_inventory))
            self.assertEqual(result["materializer_image"], IMAGE)
            return result
        self.assertEqual(actual.status.phase, "Failed", str(actual.status) + "\n" + logs)
        self.assertNotEqual(actual.status.init_container_statuses[-1].state.terminated.exit_code, 0)
        self.assertIn("Materialization failed:", logs)
        self.assertNotIn("Traceback", logs)
        self.assertFalse(any(c.state.running or c.state.terminated for c in actual.status.container_statuses or []))
        return None

    def launch_probe_fixture(self, mode):
        cm, pod = self.resources("valid")
        cm["data"]["fixture.py"] = Path(__file__).with_name("readiness_triton_server.py").read_text()
        if mode == "receipt":
            expected = json.loads(cm["data"]["readiness.json"])
            expected["receipt"]["prepared_inventory_digest"] = "sha256:" + "0" * 64
            cm["data"]["readiness.json"] = json.dumps(expected)
        main = pod["spec"]["containers"][0]
        main.update(image=IMAGE, command=["python", "/fixture/fixture.py"], args=[mode])
        main["volumeMounts"].append({"name": "materialization-plan", "mountPath": "/fixture", "readOnly": True})
        self.core.create_namespaced_config_map(self.ns, cm)
        name = pod["metadata"]["name"]
        self.core.create_namespaced_pod(self.ns, pod)
        self.addCleanup(self.core.delete_namespaced_pod, name, self.ns, grace_period_seconds=0)
        return name

    def test_replica_local_version_receipt_and_slow_load(self):
        slow, missing, receipt = [self.launch_probe_fixture(mode) for mode in ("slow", "missing", "receipt")]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            pods = [self.core.read_namespaced_pod(name, self.ns) for name in (slow, missing, receipt)]
            ready = [any(c.type == "Ready" and c.status == "True" for c in p.status.conditions or []) for p in pods]
            self.assertFalse(any(ready[1:]))
            if ready[0]:
                events = self.core.list_namespaced_event(self.ns).items

                def failed(name, message):
                    return any(e.involved_object.name == name and message in (e.message or "") for e in events)

                if (
                    failed(slow, "/versions/2/ready")
                    and failed(missing, "/versions/2/ready")
                    and failed(receipt, "receipt differs")
                ):
                    self.assertTrue(all(c.restart_count == 0 for p in pods for c in p.status.container_statuses))
                    return
            time.sleep(1)
        self.fail("Expected one recovered replica and independently observed missing-version and receipt failures")

    def test_insufficient_gpu_keeps_pod_pending(self):
        cm, pod = self.resources("valid", gpu_count=1000000)
        self.core.create_namespaced_config_map(self.ns, cm)
        name = pod["metadata"]["name"]
        self.core.create_namespaced_pod(self.ns, pod)
        self.addCleanup(self.core.delete_namespaced_pod, name, self.ns, grace_period_seconds=0)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            actual = self.core.read_namespaced_pod(name, self.ns)
            for condition in actual.status.conditions or []:
                if condition.reason == "Unschedulable" and "Insufficient nvidia.com/gpu" in condition.message:
                    self.assertEqual(actual.status.phase, "Pending")
                    return
            time.sleep(1)
        self.fail("Expected scheduler evidence of insufficient GPU capacity")

    def test_pinned_init_readonly_runtime_and_recreated_pod(self):
        # Arrange
        case = "valid"
        # Act
        first = self.execute(case, valid=True)
        second = self.execute(case, valid=True)
        # Assert
        self.assertEqual(first, second)

    def test_retained_volume_restart_without_storage(self):
        # Arrange
        case = "valid"
        # Act
        receipt = self.execute(case, valid=True, followup="restart")
        # Assert
        self.assertEqual(receipt["materializer_image"], IMAGE)

    def test_changed_helper_cannot_adopt_retained_volume(self):
        # Arrange
        case = "valid"
        # Act
        result = self.execute(case, followup="changed-helper")
        # Assert
        self.assertIsNone(result)

    def test_corrupt_retained_volume_blocks_restart(self):
        # Arrange
        case = "valid"
        # Act
        result = self.execute(case, followup="retained-corruption")
        # Assert
        self.assertIsNone(result)

    def test_interrupted_download_recovers_on_same_volume(self):
        # Arrange
        case = "valid"
        # Act
        receipt = self.execute(case, valid=True, followup="interrupted-recovery")
        # Assert
        self.assertEqual(receipt["materializer_image"], IMAGE)

    def test_rejects_missing(self):
        # Arrange
        case = "missing"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_extra(self):
        # Arrange
        case = "extra"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_unsafe(self):
        # Arrange
        case = "unsafe"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_version(self):
        # Arrange
        case = "version"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_corrupt(self):
        # Arrange
        case = "corrupt"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_interrupted(self):
        # Arrange
        case = "interrupted"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_normalization(self):
        # Arrange
        case = "normalization"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_rejects_prepared_hash(self):
        # Arrange
        case = "prepared-hash"
        # Act
        result = self.execute(case)
        # Assert
        self.assertIsNone(result)

    def test_declared_startup_rollout_gates_replica_changes(self):
        from dataclasses import asdict
        from unittest.mock import patch

        from sqlmodel import Session, SQLModel, create_engine

        from app.core.canonical import digest
        from app.db.entities import TritonInstanceEntity
        from app.exceptions import ConflictError
        from app.services.deployment import model_state
        from app.services.deployment import model_state_provider as provider_module

        provider = provider_module.ModelStateProvider(self.api)
        name = "declared-" + uuid4().hex[:8]
        labels = {"test": name}
        provider.apps.create_namespaced_deployment(self.ns, {
            "metadata": {"name": name},
            "spec": {"replicas": 1, "selector": {"matchLabels": labels}, "template": {
                "metadata": {"labels": labels},
                "spec": {"terminationGracePeriodSeconds": 1, "containers": [
                    {
                        "name": "triton", "image": IMAGE,
                        "command": ["python", "-c", "import signal; signal.pause()"],
                        "args": ["--model-control-mode=explicit"],
                    },
                ]},
            }},
        })
        self.addCleanup(provider.apps.delete_namespaced_deployment, name, self.ns)
        desired = plan(IMAGE)
        fixture = serving_release().spec.model_dump(mode="json", by_alias=True)
        fixture["repository"].update(uri="s3://fixture/valid", materializerImage=IMAGE)
        fixture["resourcesPerReplica"]["gpuCount"] = 0
        payload = {
            "apiVersion": "serving.triton-control/v1alpha1", "kind": "DevelopmentStartupRelease",
            "spec": {
                "image": fixture["image"], "repository": fixture["repository"],
                "resourcesPerReplica": fixture["resourcesPerReplica"],
                "models": [{"name": "iris", "versions": ["1"]}],
            },
        }
        binding = {
            "release": payload, "plan": desired.model_dump(mode="json"),
            "policy": policy(IMAGE, PLATFORM).model_dump(mode="json"), "limits": asdict(LIMITS),
            "storage": StorageBinding(
                bucket="fixture", prefix="valid", secret_name="fixture",
                endpoint="http://fixture:8080", allow_http=True,
            ).model_dump(mode="json"),
        }
        release = digest(payload)
        self.core.create_namespaced_config_map(self.ns, {
            "metadata": {"name": f"{name}.startup.{release.split(':')[1][:32]}"},
            "immutable": True, "data": {"startup.json": json.dumps(binding)},
        })
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(TritonInstanceEntity(
                id=1, name=name, url="http://fixture:18000", is_self_deployed=True,
                deployment_runtime="kubernetes", deployment_name=name, deployment_namespace=self.ns,
            ))
            session.commit()

            def change(action, **kwargs):
                return model_state.change(session, {"role": "admin"}, 1, action=action, **kwargs)

            def await_state(predicate):
                deadline = time.monotonic() + 160
                while time.monotonic() < deadline:
                    state = change("read")
                    if predicate(state):
                        return state
                    time.sleep(1)
                self.fail(f"Deployment did not reach expected state: {state}")

            def render(*args, **kwargs):
                # Keep the actual init container and readiness sidecar. Substitute
                # synthetic Triton HTTP responses for GPU-dependent Triton.
                cm, pod = serving_manifests(*args, **kwargs)
                cm["data"]["fixture.py"] = Path(__file__).with_name("readiness_triton_server.py").read_text()
                main = pod["spec"]["containers"][0]
                main.update(image=IMAGE, command=["python", "/fixture/fixture.py"], args=["slow"])
                main["volumeMounts"].append({"name": "materialization-plan", "mountPath": "/fixture", "readOnly": True})
                pod["spec"]["terminationGracePeriodSeconds"] = 1
                return [cm, pod]

            from contextlib import nullcontext

            with patch.object(model_state, "api_client", side_effect=lambda: nullcontext(self.api)), patch.object(
                provider_module, "serving_manifests", side_effect=render,
            ):
                await_state(lambda state: state.interactive_allowed)
                with self.assertRaises(ConflictError):
                    change("scale", replicas=2)
                declared = change("declare", release_digest=release)
                self.assertEqual(declared.mode, "declared")
                self.assertFalse(declared.can_scale_above_one)
                with self.assertRaises(ConflictError):
                    change("scale", replicas=2)
                await_state(lambda state: state.can_scale_above_one)
                change("scale", replicas=2)
                await_state(lambda state: state.replicas == 2 and state.rollout_complete)
                _, replicas = provider.snapshot(self.ns, name)
                self.assertEqual([pod.release for pod in replicas], [release, release])
                change("scale", replicas=1)
                stable = await_state(lambda state: state.can_restore)
                self.assertFalse(stable.interactive_allowed)
                change("restore")
                await_state(lambda state: state.interactive_allowed)
