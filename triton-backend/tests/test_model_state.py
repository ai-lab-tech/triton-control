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
from tests.materialization_fixtures import LIMITS, inventory, plan, policy, serving_release

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
            "spec": {"template": {
                "metadata": {"annotations": {}},
                "spec": {"containers": [{
                    "name": "triton", "command": ["/bin/bash", "-c"],
                    "args": ["exec tritonserver --model-control-mode=explicit --load-model='*'\n"],
                }]},
            }},
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

    def set_declared_rollout(self):
        self.deployment.release = RELEASE
        self.raw["spec"]["template"]["metadata"]["annotations"] = {
            RELEASE_KEY: RELEASE, ROLLBACK_KEY: "backup",
        }
        self.pods[0].release = RELEASE

    def test_legacy_migration_uses_observed_capacity_and_does_not_patch(self):
        # Arrange
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE triton_instances DROP COLUMN model_state"))
        create_tables(self.engine)
        self.deployment.desired = 4

        # Act
        state = self.invoke("read")

        # Assert
        self.assertEqual(state.replicas, 4)
        self.assertFalse(state.interactive_allowed)
        self.assertIn("model_state", {col["name"] for col in inspect(self.engine).get_columns("triton_instances")})
        with Session(self.engine) as session:
            instance = session.get(TritonInstanceEntity, 1)
            assert instance is not None
            self.assertEqual(instance.model_state, {
                "capacity_owner": "manual", "mode": "interactive", "replicas": 4, "deployment_uid": "uid",
            })
        self.provider.patch.assert_not_called()

    def test_explicit_unload_targets_verified_pod(self):
        # Arrange
        with Session(self.engine) as session, patch.object(models, "TritonService") as triton:
            unload = triton.return_value.unload_model = AsyncMock()

            # Act
            asyncio.run(models.unload_model(session, CLAIMS, 1, "second-model"))

            # Assert
            unload.assert_awaited_once_with("second-model")
            triton.assert_called_once_with("http://192.0.2.1:8000", False, "")

    def test_poll_mode_disables_interactive_commands_but_can_declare(self):
        # Arrange
        container = self.raw["spec"]["template"]["spec"]["containers"][0]
        container["args"] = [
            "exec tritonserver '--model-repository=s3://models/ --model-control-mode=explicit sample' "
            "--model-control-mode=poll --repository-poll-secs=15\n",
        ]

        # Act
        state = self.invoke("read")

        # Assert
        self.assertFalse(state.interactive_allowed)
        self.assertTrue(state.can_declare)

    def test_declare_returns_pending_state_until_current_pod_reports_release(self):
        # Arrange
        def start_rollout(raw, release):
            self.set_declared_rollout()
            self.pods[0].release = None

        self.provider.declare.side_effect = start_rollout

        # Act
        state = self.invoke("declare", release_digest=RELEASE)

        # Assert
        self.assertEqual(state.mode, "declared")
        self.assertFalse(state.interactive_allowed)
        self.assertFalse(state.rollout_complete)
        self.assertFalse(state.can_scale_above_one)

    def test_scale_above_one_is_rejected_until_declared_release_is_ready(self):
        for mode in ("interactive", "pending"):
            with self.subTest(mode=mode):
                # Arrange
                if mode == "pending":
                    self.set_declared_rollout()
                    self.pods[0].release = None

                # Act
                with self.assertRaises(ConflictError):
                    self.invoke("scale", replicas=3)

                # Assert
                self.provider.patch.assert_not_called()

    def test_scale_above_one_patches_only_replica_count_after_convergence(self):
        # Arrange
        self.set_declared_rollout()

        # Act
        self.invoke("scale", replicas=3)

        # Assert
        self.provider.patch.assert_called_once_with(self.raw, "/spec/replicas", 3)

    def test_scale_captures_the_active_pod_before_starting_a_rolling_update(self):
        # Arrange
        with Session(self.engine) as session:
            row = session.get(TritonInstanceEntity, 1)
            row.s3_enabled, row.s3_bucket, row.deployment_secret_name = True, "models", "storage"
            session.commit()
        models = ("iris",)
        with patch.object(service.replica_startup, "ready_models", return_value=models) as read:
            # Act
            self.invoke("scale", replicas=2)

        # Assert
        self.assertEqual(read.call_count, 2)
        self.provider.scale_serving.assert_called_once_with(self.raw, models, 2)
        self.provider.patch.assert_not_called()

    def test_reconcile_waits_for_ready_release_and_then_applies_pending_target(self):
        # Arrange
        self.set_declared_rollout()
        self.raw["metadata"]["annotations"] = {service.SCALE_TARGET: "2"}
        self.pods[0].ready = False
        with Session(self.engine) as session:
            row = session.get(TritonInstanceEntity, 1)
            # Act
            service.reconcile_scale(session, row)
            self.provider.patch.assert_not_called()
            self.pods[0].ready = True
            service.reconcile_scale(session, row)

        # Assert
        self.provider.patch.assert_called_once_with(self.raw, "/spec/replicas", 2, clear_target=True)

    def test_load_is_blocked_in_declared_mode(self):
        # Arrange
        self.set_declared_rollout()
        with Session(self.engine) as session, patch.object(models, "TritonService") as triton:
            # Act
            with self.assertRaises(ConflictError):
                asyncio.run(models.load_model(session, CLAIMS, 1, "second-model"))

            # Assert
            triton.assert_not_called()

    def test_restore_is_blocked_while_surplus_pod_terminates(self):
        # Arrange
        self.set_declared_rollout()
        self.pods.append(self.pods[0].model_copy(update={"uid": "surplus", "terminating": True}))

        # Act
        with self.assertRaises(ConflictError):
            self.invoke("restore")

        # Assert
        self.provider.restore.assert_not_called()

    def test_restore_ignores_failed_history_and_waits_for_interactive_rollout(self):
        # Arrange
        self.set_declared_rollout()
        self.pods.append(self.pods[0].model_copy(update={"uid": "history", "phase": "Failed", "ready": False}))

        def start_restore(raw, backup):
            self.deployment.release = None
            self.deployment.stale = True
            self.raw["spec"]["template"]["metadata"]["annotations"] = {}

        self.provider.restore.side_effect = start_restore

        # Act
        state = self.invoke("restore")

        # Assert
        self.assertFalse(state.interactive_allowed)
        self.provider.restore.assert_called_once_with(self.raw, "backup")

    def test_failed_single_replica_rollout_can_restore_without_allowing_model_commands(self):
        # Arrange
        self.set_declared_rollout()
        self.deployment.ready = 0
        self.deployment.conditions = [
            PodCondition(type="Progressing", status="False", reason="ProgressDeadlineExceeded"),
        ]

        # Act
        state = self.invoke("read")

        # Assert
        self.assertTrue(state.can_restore)
        self.assertFalse(state.interactive_allowed)
        self.assertFalse(state.can_scale_above_one)

    def test_malformed_release_marker_cannot_enable_replica_scaling(self):
        # Arrange
        self.set_declared_rollout()
        self.deployment.release = self.pods[0].release = "sha256:invalid"

        # Act
        state = self.invoke("read")

        # Assert
        self.assertEqual(state.mode, "declared")
        self.assertIsNone(state.release_digest)
        self.assertFalse(state.can_scale_above_one)

    def test_autoscaler_owns_capacity(self):
        # Arrange
        self.provider.has_autoscaler.return_value = True

        # Act
        state = self.invoke("read")

        # Assert
        self.assertEqual(state.capacity_owner, "autoscaler")
        self.assertFalse(state.can_declare)

    def test_production_policy_rejects_replica_changes(self):
        # Arrange
        with patch.object(service.installation_policy, "mode", return_value="production"):
            # Act
            with self.assertRaises(ForbiddenError):
                self.invoke("scale", replicas=3)

        # Assert
        self.provider.patch.assert_not_called()

    def test_new_workload_uid_is_rejected_after_legacy_observation(self):
        # Arrange
        self.invoke("read")
        self.raw["metadata"]["uid"] = "replacement"
        self.deployment.uid = "replacement"

        # Act
        with self.assertRaisesRegex(ConflictError, "Deployment UID changed"):
            self.invoke("read")

        # Assert
        self.provider.patch.assert_not_called()


class StartupProviderTests(unittest.TestCase):
    def setUp(self):
        sample = serving_release().spec.model_dump(mode="json", by_alias=True)
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
        # Arrange
        self.deployment["spec"]["template"]["metadata"]["annotations"] = None

        # Act
        self.provider.declare(self.deployment, self.target)

        # Assert
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
        # Arrange
        self.binding["release"]["spec"]["models"][0]["versions"] = ["2"]
        target = digest(self.binding["release"])

        # Act
        with self.assertRaises(ConflictError):
            self.provider.declare(self.deployment, target)

        # Assert
        self.provider.patch.assert_not_called()
        self.provider.ensure_config.assert_not_called()

    def test_file_at_version_path_is_rejected_before_rendering(self):
        # Arrange
        files = {"iris/1": b"not a directory", "iris/config.pbtxt": b'name: "iris"'}
        inventory_data = inventory(files).model_dump(mode="json")
        self.binding["plan"].update(
            prepared_inventory=inventory_data,
            source_inventory=inventory_data,
            objects=[{"path": path, "version_id": "v1"} for path in files],
            normalization={},
        )

        # Act
        with self.assertRaises(ConflictError) as error:
            self.provider.declare(self.deployment, self.target)

        # Assert
        self.assertIn("a file at a model version path", str(error.exception.__cause__))
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
