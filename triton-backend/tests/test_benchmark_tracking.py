"""Task benchmark evidence and opt-in local Dev MLflow integration."""

import json
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from sqlmodel import Session, SQLModel, create_engine

from app.core.canonical import inventory_digest
from app.db.entities import ModelPerfJobEntity, TritonInstanceEntity
from app.exceptions import ConflictError, ForbiddenError
from app.schemas.lineage import FileEntry, InventoryV1
from app.services.perf_analyzer import benchmark_tracking as tracking
from app.services.perf_analyzer import jobs, provenance

OUTPUT = "private raw log\n" + tracking.CSV_MARKER + "\nConcurrency,Inferences/Second,p99 latency\n1,12,20\n2,19,\n"


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        self.session = Session(engine)
        self.addCleanup(self.session.close)
        self.instance = TritonInstanceEntity(id=1, name="served", url="http://triton")
        self.session.add(self.instance)
        self.session.commit()

    def job(self):
        job = ModelPerfJobEntity(
            id=uuid4().hex,
            instance_id=1,
            model_name="toy",
            model_version="1",
            image="sdk",
            namespace="test",
            job_name="job-" + uuid4().hex,
            finished_at=datetime.utcnow(),
            state="succeeded",
            mlflow_state="pending",
            output=OUTPUT,
            input_data="private input",
        )
        self.session.add(job)
        return job

    def test_measurements_use_actual_concurrency_and_leave_missing_values_absent(self):
        result = tracking.measurements(OUTPUT)
        self.assertEqual(
            result,
            [
                (1, "throughput_inferences_per_second", 12),
                (1, "latency_p99_us", 20),
                (2, "throughput_inferences_per_second", 19),
            ],
        )

    def test_non_task_servings_hide_option_and_reject_forged_opt_in(self):
        self.assertIsNone(provenance.option(self.session, {}, self.instance, "toy", "1"))
        with self.assertRaises(ConflictError):
            provenance.resolve(self.session, {}, self.instance, "toy", "1", submit=True)

    def test_unassigned_engineer_cannot_select_logging(self):
        self.instance.preparation_task_id = str(uuid4())
        with patch.object(provenance.workspace, "authorize", side_effect=ForbiddenError("Assignee required")):
            self.assertFalse(provenance.option(self.session, {}, self.instance, "toy", "1").available)
            with self.assertRaises(ForbiddenError):
                provenance.resolve(self.session, {}, self.instance, "toy", "1", submit=True)

    def test_submission_pins_measured_inventory_and_rejects_a_different_version(self):
        self.instance.preparation_task_id = str(uuid4())
        inventory = InventoryV1(files=(FileEntry(path="toy/1/model", size=3, digest="sha256:" + "a" * 64),))
        self.instance.perf_candidate = {
            "revision": "revision",
            "inventory": inventory_digest(inventory),
            "versions": [["toy", "1"]],
        }
        source = SimpleNamespace(record_id="models:/m-source", installation_id="dev")
        inputs = SimpleNamespace(snapshot=SimpleNamespace(kind="mlflow", origin=source))
        with (
            patch.object(provenance.workspace, "authorize", return_value=7),
            patch.object(provenance.workspace, "load_inputs", return_value=inputs),
            patch.object(provenance.workspace, "authorize_inputs"),
            patch.object(provenance.tracking, "resolve_model", side_effect=lambda *args: {"model_id": "m-converted"}),
            patch.object(provenance, "verify_workload", return_value="runtime"),
            patch.object(provenance, "repository") as repository,
        ):
            repository.return_value.capture.return_value.inventory = inventory
            snapshot = provenance.resolve(self.session, {"email": "engineer"}, self.instance, "toy", "1", submit=True)
            self.assertEqual(snapshot["task_id"], self.instance.preparation_task_id)
            self.assertEqual(snapshot["inventory"], inventory_digest(inventory))
            self.assertEqual(snapshot["runtime"], "runtime")
            with self.assertRaisesRegex(ConflictError, "model/version"):
                provenance.resolve(self.session, {}, self.instance, "toy", "2", submit=True)
            self.instance.perf_candidate = {
                "revision": "revision",
                "inventory": "sha256:" + "b" * 64,
                "versions": [["toy", "1"]],
            }
            with self.assertRaisesRegex(ConflictError, "inventory"):
                provenance.resolve(self.session, {}, self.instance, "toy", "1", submit=True)

    def test_replacing_an_object_changes_launch_revision_even_if_the_content_is_restored(self):
        client = Mock()
        client.meta.endpoint_url = "http://dev-store"
        source = SimpleNamespace(client=client, bucket="dev", prefix="candidate/")
        pages = client.get_paginator.return_value
        initial = {"Key": "candidate/model", "VersionId": "first", "IsLatest": True}
        pages.paginate.return_value = [{"Versions": [initial]}]
        original = provenance.inspect_revision(source)

        pages.paginate.return_value = [{"Versions": [initial, {**initial, "VersionId": "second", "IsLatest": False}]}]

        self.assertNotEqual(provenance.inspect_revision(source), original)

    def test_failed_cancelled_and_changed_runtime_never_enter_sync_queue(self):
        run = self.job()
        run.mlflow_snapshot = {"runtime": "original"}
        with patch.object(provenance, "verify_workload", return_value="changed"):
            for state in ("failed", "cancelled", "succeeded"):
                jobs.finish(self.session, run, state, "done")
                self.session.commit()
                self.assertEqual(tracking.pending(self.session), [])

    @unittest.skipUnless(os.getenv("PERF_MLFLOW_TEST_URL"), "needs disposable local MLflow")
    def test_real_model_association_and_retry_after_accepted_create_and_metrics(self):
        with (
            patch.object(tracking.installer, "get_proxy_server_url", return_value=os.environ["PERF_MLFLOW_TEST_URL"]),
            patch.object(tracking.proxy, "_proxy_http_sync", tracking.proxy._direct_proxy_http_sync),
        ):
            api = tracking.api
            experiment = api(self.session, "experiments/create", {"name": "perf-" + uuid4().hex})["experiment_id"]
            source = api(self.session, "logged-models", {"experiment_id": experiment, "name": "scientist"})["model"][
                "info"
            ]
            uri = "models:/" + source["model_id"]
            inventory = "sha256:" + uuid4().hex * 2
            tags = {
                "triton.output.inventory": inventory,
                "triton.source.model_uri": uri,
                "triton.source.installation": "dev",
                "triton.conversion.run_id": "conversion",
            }
            derived = api(
                self.session,
                "logged-models",
                {
                    "experiment_id": experiment,
                    "name": "converted",
                    "model_type": "triton",
                    "tags": [{"key": k, "value": v} for k, v in tags.items()],
                },
            )["model"]["info"]
            response = tracking.proxy._direct_proxy_http_sync(
                os.environ["PERF_MLFLOW_TEST_URL"],
                "api/2.0/mlflow/logged-models/" + derived["model_id"],
                "PATCH",
                {"Content-Type": "application/json"},
                [],
                json.dumps({"model_id": derived["model_id"], "status": "LOGGED_MODEL_READY"}).encode(),
            )
            self.assertEqual(response.status_code, 200, response.body)
            snapshot = tracking.resolve_model(self.session, {"inventory": inventory}, uri, "dev")
            snapshot.update(
                inventory=inventory,
                runtime={
                    "fingerprint": "runtime",
                    "deployment_uid": "uid",
                    "generation": 1,
                    "images": ["triton@sha256:" + "a" * 64],
                },
                task_id="task",
                revision="rev",
                creator="engineer",
            )
            self.assertEqual(snapshot["model_id"], derived["model_id"])
            ids = set()
            for endpoint in ("runs/create", "runs/log-batch"):
                run = self.job()
                run.mlflow_snapshot = snapshot
                self.session.commit()
                lost = Mock()

                def ambiguous(session, path, body=None):
                    result = api(session, path, body)
                    if path == endpoint and not lost.called:
                        lost()
                        raise TimeoutError
                    return result

                with patch.object(tracking, "api", side_effect=ambiguous):
                    tracking.sync(self.session, run.id)
                    tracking.sync(self.session, run.id)
                self.assertEqual(run.mlflow_state, "synced")
                matches = api(
                    self.session,
                    "runs/search",
                    {"experiment_ids": [experiment], "filter": f"tags.`{tracking.JOB_TAG}` = '{run.id}'"},
                )["runs"]
                self.assertEqual(len(matches), 1)
                record = matches[0]
                self.assertNotIn("private", str(record))
                self.assertIn(snapshot["source_url"], str(record["data"]["tags"]))
                history = api(
                    self.session,
                    "metrics/get-history",
                    run_id=run.mlflow_run_id,
                    metric_key="throughput_inferences_per_second",
                )["metrics"]
                self.assertEqual(sorted(int(m["step"]) for m in history), [1, 2])
                model = api(self.session, "logged-models/" + derived["model_id"])["model"]
                self.assertTrue(any(m["run_id"] == run.mlflow_run_id for m in model["data"]["metrics"]))
                ids.add(run.mlflow_run_id)
            self.assertEqual(len(ids), 2)
