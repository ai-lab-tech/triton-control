"""Structured performance measurements for the installation's Dev MLflow."""

import csv
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, col, select

from app.db.entities import ModelPerfJobEntity
from app.exceptions import ConflictError, ServiceUnavailableError
from app.repositories import perf_jobs
from app.services.mlflow import config, installer, proxy

CSV_MARKER = "TRITON_CONTROL_PERF_CSV_V1"
JOB_TAG = "triton.benchmark.job_id"


def api(session: Session, endpoint: str, body: dict[str, Any] | None = None, **query: str) -> dict[str, Any]:
    try:
        response = proxy._proxy_http_sync(
            installer.get_proxy_server_url(session),
            "api/2.0/mlflow/" + endpoint,
            "POST" if body is not None else "GET",
            {"Content-Type": "application/json"},
            list(query.items()),
            json.dumps(body).encode() if body is not None else b"",
            timeout=5,
        )
        if response.status_code == 404:
            raise ConflictError("The referenced Dev MLflow record is unavailable.")
        if response.status_code != 200:
            raise ValueError
        return dict(json.loads(bytes(response.body)))
    except ConflictError:
        raise
    except Exception:
        raise ServiceUnavailableError("Dev MLflow could not complete the metadata request.") from None


def stable_id(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise ConflictError("Invalid immutable MLflow identifier.")
    return value


def ui(experiment: str, kind: str, identifier: str) -> str:
    return f"{config.base_path()}#/experiments/{stable_id(experiment)}/{kind}/{stable_id(identifier)}"


def find_candidate(session: Session, inventory: str) -> dict[str, Any]:
    matches = []
    search_params: dict[str, Any] = {"max_results": 100}
    while True:
        page = api(session, "experiments/search", search_params)
        ids = [e["experiment_id"] for e in page.get("experiments", [])]
        if ids:
            result = api(
                session,
                "logged-models/search",
                {
                    "experiment_ids": ids,
                    "filter": f"tags.`triton.output.inventory` = '{inventory}'",
                    "max_results": 2,
                },
            )
            matches.extend(result.get("models", []))
        next_page = page.get("next_page_token")
        if not next_page or len(matches) > 1:
            break
        search_params["page_token"] = next_page
    if len(matches) != 1:
        raise ConflictError("No unique converted Dev MLflow model matches this candidate.")
    return dict(matches[0]["info"])


def resolve_model(session: Session, binding: dict[str, Any], source: str, installation: str) -> dict[str, Any]:
    uri = binding.get("derived_uri")
    if uri:
        if not uri.startswith("models:/m-"):
            raise ConflictError("The converted model has no immutable logged-model ID.")
        info = api(session, "logged-models/" + stable_id(uri[8:]))["model"]["info"]
    else:
        info = find_candidate(session, binding["inventory"])
    if info.get("status") != "LOGGED_MODEL_READY" or info.get("model_type") != "triton":
        raise ConflictError("The converted Triton model is not ready in Dev MLflow.")
    tags = {t["key"]: t["value"] for t in info.get("tags", [])}
    if (
        tags.get("triton.output.inventory") != binding["inventory"]
        or tags.get("triton.source.model_uri") != source
        or tags.get("triton.source.installation") != installation
        or not tags.get("triton.conversion.run_id")
        or (binding.get("lineage_digest") and tags.get("triton.publication.content") != binding["lineage_digest"])
    ):
        raise ConflictError("The converted model does not match the authorized task source.")
    if source.startswith("models:/m-"):
        source_id = stable_id(source[8:])
        record = api(session, "logged-models/" + source_id)["model"]["info"]
        source_url = ui(record["experiment_id"], "models", source_id)
    elif re.fullmatch(r"runs:/[a-zA-Z0-9_-]+/[^?#]+", source):
        source_id = stable_id(source.split("/")[1])
        record = api(session, "runs/get", run_id=source_id)["run"]["info"]
        source_url = ui(record["experiment_id"], "runs", source_id)
    else:
        raise ConflictError("The task has no immutable MLflow source.")
    return {
        "model_id": stable_id(info["model_id"]),
        "experiment": stable_id(info["experiment_id"]),
        "source": source,
        "source_url": source_url,
        "source_installation": installation,
    }


def measurements(output: str) -> list[tuple[int, str, float]]:
    _, marker, table = output.rpartition(CSV_MARKER + "\n")
    if not marker:
        raise ValueError("Missing benchmark CSV")
    values = []
    seen = set()
    for row in csv.DictReader(table.strip().splitlines()):
        step = int(row["Concurrency"])
        if step < 1 or step in seen:
            raise ValueError("Invalid concurrency")
        seen.add(step)
        names = {
            "Inferences/Second": "throughput_inferences_per_second",
            "Avg latency": "latency_mean_us",
            "Client Send": "client_send_us",
            "Client Recv": "client_receive_us",
            "Network+Server Send/Recv": "network_server_send_receive_us",
            "Server Queue": "server_queue_us",
            "Server Compute Input": "server_compute_input_us",
            "Server Compute Infer": "server_compute_infer_us",
            "Server Compute Output": "server_compute_output_us",
        }
        names.update(
            {
                key: "latency_" + key.split()[0] + "_us"
                for key in row
                if key and re.fullmatch(r"p[0-9]{1,2}(?:\.[0-9]+)? latency", key)
            }
        )
        if not row.get("Inferences/Second"):
            raise ValueError("Missing throughput")
        for column, key in names.items():
            if row.get(column):
                value = float(row[column])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Invalid measurement")
                values.append((step, key, value))
    if not values:
        raise ValueError("Empty benchmark CSV")
    return values


def pending(session: Session) -> list[str]:
    return list(
        session.exec(
            select(ModelPerfJobEntity.id)
            .where(
                col(ModelPerfJobEntity.mlflow_state).in_(["pending", "creating_run", "syncing"]),
                (col(ModelPerfJobEntity.mlflow_retry_at).is_(None))
                | (col(ModelPerfJobEntity.mlflow_retry_at) <= datetime.utcnow()),
            )
            .limit(100)
        ).all()
    )


def sync(session: Session, job_id: str) -> None:
    job = perf_jobs.lock_run(session, job_id, skip_locked=True)
    if not job or job.state != "succeeded" or job.mlflow_state not in {"pending", "creating_run", "syncing"}:
        return
    s = job.mlflow_snapshot
    if not s or not job.finished_at:
        return
    stamp = int(job.finished_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    try:
        if not job.mlflow_run_id:
            tags = {
                JOB_TAG: job_id,
                "triton.source.model_uri": s["source"],
                "triton.source.url": s["source_url"],
                "triton.derived.model_id": s["model_id"],
                "mlflow.user": s["creator"],
            }
            found = api(
                session,
                "runs/search",
                {"experiment_ids": [s["experiment"]], "filter": f"tags.`{JOB_TAG}` = '{job.id}'", "max_results": 2},
            ).get("runs", [])
            recovered_tags = {t["key"]: t["value"] for t in found[0]["data"].get("tags", [])} if found else {}
            if found and (
                len(found) != 1
                or job.mlflow_state != "creating_run"
                or any(recovered_tags.get(k) != v for k, v in tags.items())
            ):
                job.mlflow_state = "ambiguous"
                session.commit()
                return
            if found:
                remote = found[0]
            elif job.mlflow_state == "pending":
                # CreateRun has no idempotency key. Persist intent before sending it.
                job.mlflow_state = "creating_run"
                session.commit()
                job = perf_jobs.lock_run(session, job_id)
                if job is None:
                    return
                remote = api(
                    session,
                    "runs/create",
                    {
                        "experiment_id": s["experiment"],
                        "start_time": stamp,
                        "run_name": f"Perf Analyzer {job.model_name} {job_id[:8]}",
                        "tags": [{"key": k, "value": v} for k, v in tags.items()],
                    },
                )["run"]
            else:
                raise ServiceUnavailableError("Waiting for MLflow to confirm run creation.")
            job.mlflow_run_id = stable_id(remote["info"]["run_id"])
            job.mlflow_state = "syncing"
        metrics = [
            {
                "key": key,
                "value": value,
                "step": step,
                "timestamp": stamp,
                "model_id": s["model_id"],
                "run_id": job.mlflow_run_id,
            }
            for step, key, value in measurements(job.output)
        ]
        params = {
            "model": job.model_name,
            "version": job.model_version,
            "batch_size": job.batch_size,
            "concurrency_range": job.concurrency_range,
            "measurement_request_count": job.measurement_request_count,
            "measurement_mode": "count_windows",
            "analyzer_image": job.image,
            "streaming": "--streaming" in job.command,
            "async": "--async" in job.command,
            "candidate_inventory": s["inventory"],
            "runtime_fingerprint": s["runtime"]["fingerprint"],
            "deployment_uid": s["runtime"]["deployment_uid"],
            "deployment_generation": s["runtime"]["generation"],
            "serving_images": ", ".join(s["runtime"]["images"]),
            "task": s["task_id"],
            "candidate_revision": s["revision"],
        }
        for offset in range(0, len(metrics), 500):
            api(
                session,
                "runs/log-batch",
                {
                    "run_id": job.mlflow_run_id,
                    "metrics": metrics[offset : offset + 500],
                    "params": [{"key": k, "value": str(v)} for k, v in params.items()] if offset == 0 else [],
                },
            )
        api(session, "runs/update", {"run_id": job.mlflow_run_id, "status": "FINISHED", "end_time": stamp})
        job.mlflow_state = "synced"
    except Exception:
        if job is not None:
            job.mlflow_retry_at = datetime.utcnow() + timedelta(seconds=60)
    session.commit()
