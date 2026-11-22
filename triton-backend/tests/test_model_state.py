"""Model-state transition, migration and provider admission behavior."""

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app.core.canonical import digest
from app.db.entities import TritonInstanceEntity
from app.db.schema import create_tables
from app.exceptions import ConflictError, ForbiddenError
from app.repositories.instances import lock_model_state
from app.schemas.model_state import DevelopmentStartupRelease
from app.schemas.pods import DeploymentState, PodCondition, PodState
from app.services.deployment import model_state as service
from app.services.deployment.model_state_provider import ROLLBACK_KEY, ModelStateProvider
from app.services.deployment.pod_provider import RELEASE_KEY
from app.services.triton import models
from tests.materialization_fixtures import LIMITS, plan, policy
from tests import test_materialization as materializer_tests

RELEASE = "sha256:" + "a" * 64
CLAIMS = {"role": "admin"}


class ModelStateTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.engine = create_engine(f"sqlite:///{tmp}/state.db")
        self.addCleanup(self.engine.dispose)
        create_tables(self.engine)
        with Session(self.engine) as session:
            session.add(TritonInstanceEntity(
                id=1, name="test", url="http://test:18000", is_self_deployed=True,
                deployment_runtime="kubernetes", deployment_name="test", deployment_namespace="test",
            ))
            session.commit()
        self.raw = {
            "metadata": {"uid": "uid", "generation": 1, "namespace": "test", "name": "test"},
            "spec": {"template": {"metadata": {"annotations": {}}}},
        }
        self.deployment = DeploymentState(
            uid="uid", name="test", generation=1, observed_generation=1, stale=False,
            desired=1, ready=1, updated=1, available=1, revision="1",
        )
        self.pods = [PodState(uid="pod", name="test-pod", phase="Running",
                              ready=True, restarts=0, terminating=False, current_revision=True)]
        self.provider = self.enterContext(patch.object(service, "ModelStateProvider")).return_value
        self.enterContext(patch.object(service, "api_client"))
        self.provider.deployment.return_value = self.raw
        self.provider.snapshot.return_value = (self.deployment, self.pods)
        self.provider.has_autoscaler.return_value = False
        self.provider.verify.return_value = {"metadata": {}, "status": {
            "podIP": "192.0.2.1", "conditions": [{"type": "Ready", "status": "True"}],
        }}

    def invoke(self, action, **args):
        with Session(self.engine) as session:
            return service.change(session, CLAIMS, 1, action=action, **args)

    def declare(self):
        self.deployment.release = RELEASE
        self.raw["spec"]["template"]["metadata"]["annotations"] = {
            RELEASE_KEY: RELEASE, ROLLBACK_KEY: "backup",
        }
        self.pods[0].release = RELEASE

    def test_legacy_migration_uses_observed_capacity_and_does_not_patch(self):
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE triton_instances DROP COLUMN model_state"))
        create_tables(self.engine)
        self.assertIn("model_state", {col["name"] for col in inspect(self.engine).get_columns("triton_instances")})
        self.deployment.desired = 4
        state = self.invoke("read")
        self.assertEqual(state.replicas, 4)
        self.assertFalse(state.interactive_allowed)
        with Session(self.engine) as session:
            instance = session.get(TritonInstanceEntity, 1)
            assert instance is not None
            self.assertEqual(instance.model_state, {
                "capacity_owner": "manual", "mode": "interactive", "replicas": 4, "deployment_uid": "uid",
            })
        self.provider.patch.assert_not_called()

    def test_explicit_rollout_after_interactive_unload_gates_scale(self):
        # Only the immutable bundle supplies startup models, regardless of the old runtime or cached index.
        self.assertTrue(self.invoke("read").interactive_allowed)
        with self.assertRaises(ConflictError):
            self.invoke("scale", replicas=3)
        with Session(self.engine) as session, patch.object(models, "TritonService") as triton:
            unload = triton.return_value.unload_model = AsyncMock()
            asyncio.run(models.unload_model(session, CLAIMS, 1, "second-model"))
            unload.assert_awaited_once_with("second-model")
            triton.assert_called_once_with("http://192.0.2.1:8000", False, "")

        def start_rollout(raw, release):
            self.declare()
            self.pods[0].release = None

        self.provider.declare.side_effect = start_rollout
        response = self.invoke("declare", release_digest=RELEASE)
        self.assertEqual(response.mode, "declared")
        self.assertFalse(response.interactive_allowed)
        self.assertFalse(response.rollout_complete)
        with self.assertRaises(ConflictError):
            self.invoke("scale", replicas=3)
        self.provider.patch.assert_not_called()
        with Session(self.engine) as session, patch.object(models, "TritonService") as triton:
            with self.assertRaises(ConflictError):
                asyncio.run(models.load_model(session, CLAIMS, 1, "second-model"))
            triton.assert_not_called()
        self.pods[0].release = RELEASE
        self.invoke("scale", replicas=3)
        self.provider.patch.assert_called_once_with(self.raw, "/spec/replicas", 3)

    def test_scale_down_keeps_declared_mode_and_waits_for_terminating_pods(self):
        self.declare()
        self.pods.append(self.pods[0].model_copy(update={"uid": "surplus", "terminating": True}))
        state = self.invoke("read")
        self.assertFalse(state.interactive_allowed)
        self.assertFalse(state.can_restore)
        with self.assertRaises(ConflictError):
            self.invoke("restore")
        self.pods.pop()
        self.pods.append(self.pods[0].model_copy(update={"uid": "history", "phase": "Failed", "ready": False}))
        self.assertTrue(self.invoke("read").can_restore)
        self.assertFalse(self.invoke("read").interactive_allowed)

        def restore(raw, backup):
            self.deployment.release = None
            self.deployment.stale = True
            self.raw["spec"]["template"]["metadata"]["annotations"] = {}

        self.provider.restore.side_effect = restore
        self.assertFalse(self.invoke("restore").interactive_allowed)
        self.provider.restore.assert_called_once_with(self.raw, "backup")
        self.deployment.stale = False
        self.assertTrue(self.invoke("read").interactive_allowed)

    def test_failed_single_replica_rollout_can_restore_without_allowing_model_commands(self):
        self.declare()
        self.deployment.ready = 0
        self.deployment.conditions = [
            PodCondition(type="Progressing", status="False", reason="ProgressDeadlineExceeded"),
        ]
        state = self.invoke("read")
        self.assertTrue(state.can_restore)
        self.assertFalse(state.interactive_allowed)
        self.assertFalse(state.can_scale_above_one)
        self.invoke("restore")
        self.provider.restore.assert_called_once_with(self.raw, "backup")

    def test_capacity_owner_uid_and_production_ceiling(self):
        self.provider.has_autoscaler.return_value = True
        self.assertEqual(self.invoke("read").capacity_owner, "autoscaler")
        with self.assertRaises(ConflictError):
            self.invoke("declare", release_digest=RELEASE)
        with patch.object(service.installation_policy, "mode", return_value="production"):
            self.assertEqual(self.invoke("read").capacity_owner, "source")
            with self.assertRaises(ForbiddenError):
                self.invoke("scale", replicas=3)
        self.raw["metadata"]["uid"] = "replacement"
        self.deployment.uid = "replacement"
        with self.assertRaises(ConflictError):
            self.invoke("read")


class StartupProviderTests(unittest.TestCase):
    def setUp(self):
        initial = materializer_tests.MaterializerPodTests().release()
        sample = initial.spec.model_dump(mode="json", by_alias=True)
        self.release = DevelopmentStartupRelease.model_validate({
            "apiVersion": "serving.triton-control/v1alpha1", "kind": "DevelopmentStartupRelease",
            "spec": {
                "image": sample["image"], "repository": sample["repository"],
                "resourcesPerReplica": sample["resourcesPerReplica"],
                "models": [{"name": "iris", "versions": ["1"]}],
            },
        })
        self.target = digest(self.release.model_dump(mode="json", by_alias=True))
        self.binding = {
            "release": self.release.model_dump(mode="json", by_alias=True),
            "plan": plan().model_dump(mode="json"),
            "policy": policy().model_dump(mode="json"),
            "storage": {"bucket": "models", "prefix": "repository", "secret_name": "read-only-models"},
            "limits": asdict(LIMITS),
        }
        self.provider = ModelStateProvider(MagicMock())
        self.provider.immutable_config = MagicMock(side_effect=lambda *_: {
            "data": {"startup.json": json.dumps(self.binding)},
        })
        self.provider.ensure_config = MagicMock()
        self.provider.patch = MagicMock()
        self.deployment = {
            "metadata": {"uid": "workload-uid", "name": "iris", "namespace": "test"},
            "spec": {"template": {
                "metadata": {"labels": {"app": "triton"}},
                "spec": {"containers": [{"name": "triton", "args": ["--model-control-mode=explicit"]}]},
            }},
        }

    def test_frozen_template_replaces_mutable_repository_and_verifies_versions(self):
        self.provider.declare(self.deployment, self.target)
        _, path, template = self.provider.patch.call_args.args
        self.assertEqual(path, "/spec/template")
        self.assertEqual(template["metadata"]["labels"], {"app": "triton"})
        self.assertEqual(template["metadata"]["annotations"][RELEASE_KEY], self.target)
        self.assertIn("--model-control-mode=none", template["spec"]["containers"][0]["args"])
        self.assertEqual(template["spec"]["containers"][1]["name"], "release-readiness")
        config = self.provider.ensure_config.call_args.args[1]
        self.assertTrue(config["immutable"])
        self.assertEqual(json.loads(config["data"]["readiness.json"])["models"], [{"name": "iris", "versions": ["1"]}])

    def test_rejects_mismatched_preparation_before_touching_deployment(self):
        self.binding["release"]["spec"]["models"][0]["versions"] = ["2"]
        target = digest(self.binding["release"])
        with self.assertRaises(ConflictError):
            self.provider.declare(self.deployment, target)
        self.provider.patch.assert_not_called()
        self.provider.ensure_config.assert_not_called()


@unittest.skipUnless(os.getenv("TEAMS_TEST_DATABASE_URL"), "Requires an isolated PostgreSQL test database")
class ModelStatePostgresTests(unittest.TestCase):
    def test_concurrent_model_state_mutation_is_rejected_until_transaction_ends(self):
        engine = create_engine(os.environ["TEAMS_TEST_DATABASE_URL"])
        self.addCleanup(engine.dispose)
        instance_id = int(uuid4().hex[:7], 16)
        with Session(engine) as current, Session(engine) as contender:
            lock_model_state(current, instance_id)
            with self.assertRaises(ConflictError):
                lock_model_state(contender, instance_id)
            contender.rollback()
            current.commit()
            lock_model_state(contender, instance_id)
            self.assertEqual(contender.execute(text("SELECT 1")).scalar_one(), 1)
