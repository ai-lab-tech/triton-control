"""Durable per-model execution with serialized Kubernetes reconciliation."""

from __future__ import annotations

import logging
from datetime import datetime
from threading import Event, Thread
from typing import Any
from uuid import uuid4

from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.db.database import session_factory
from app.db.entities import ModelPerfJobEntity, PerfAnalyzerRunEntity
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.repositories import perf_analyzer as results
from app.repositories import perf_jobs as repo
from app.schemas.perf_analyzer import RunPerfAnalyzerRequest
from app.schemas.perf_jobs import ModelPerfRunResponse, ModelPerfStatusResponse, StartModelPerfRequest
from app.services.access import get_instance_or_404
from app.services.perf_analyzer import installer as commands
from app.services.perf_analyzer.jobs_kubernetes import Jobs, deadline_seconds, default_image, pod_message

logger = logging.getLogger(__name__)


def dto(run: ModelPerfJobEntity) -> ModelPerfRunResponse:
    return ModelPerfRunResponse.model_validate(run, from_attributes=True)


def start(
    session: Session, claims: dict[str, Any], instance_id: int, model_name: str, request: StartModelPerfRequest,
) -> ModelPerfRunResponse:
    instance = get_instance_or_404(session, instance_id, claims)
    if results.get(session):
        raise ConflictError("Legacy analyzer retirement is in progress. Retry when its workload has stopped.")
    existing = repo.active(session, instance_id, model_name)
    if existing:
        raise ConflictError(f"A performance run is already active for this model: {existing.id}")
    target = RunPerfAnalyzerRequest(
        instance_id=instance_id, model_name=model_name,
        **request.model_dump(exclude={"image", "dockerconfigjson"}),
    )
    config = commands._fetch_triton_model_config(instance, model_name=model_name, model_version=target.model_version)
    if config is None:
        raise BadRequestError("Cannot read the selected model/version configuration from Triton")
    decoupled = commands._model_config_requires_decoupled_perf_analyzer_mode(config)
    prepared_input = commands._prepare_input_data_for_perf_analyzer(target.input_data, decoupled=decoupled)
    input_arg = commands._direct_perf_input_argument(prepared_input or "")
    if prepared_input and input_arg is None:
        input_arg = "/perf-input/input.json"
    run_id = uuid4().hex
    namespace = commands._perf_analyzer_namespace()
    run = ModelPerfJobEntity(
        id=run_id, instance_id=instance_id, model_name=target.model_name, model_version=target.model_version,
        image=request.image or default_image(), namespace=namespace, job_name=f"model-perf-{run_id}",
        pull_secret=bool(request.dockerconfigjson), input_data=prepared_input,
        batch_size=commands._effective_perf_analyzer_batch_size(target, decoupled=decoupled),
        concurrency_range=target.concurrency_range, measurement_request_count=target.measurement_request_count,
        command=commands._run_command(target, instance, perf_analyzer_namespace=namespace,
                                      input_data_arg=input_arg, decoupled=decoupled),
    )
    repo.reserve(session, run)
    locked = repo.lock_run(session, run_id)
    if locked is None:
        raise NotFoundError("Performance run disappeared during preparation")
    if locked.state == "creating":
        api = None
        try:
            api = Jobs()
            api.prepare(locked, request.dockerconfigjson)
            locked.prepared = True
            locked.message = "Benchmark queued for Job creation."
        except Exception as exc:
            # Secret API exceptions can contain credentials; do not echo their bodies.
            code = str(exc.status) if isinstance(exc, ApiException) else type(exc).__name__
            finish(session, locked, "failed", f"Could not prepare benchmark input or registry credentials ({code}).")
        finally:
            if api:
                api.close()
    session.add(locked)
    session.commit()
    session.refresh(locked)
    return dto(locked)


def get_run(
    session: Session, claims: dict[str, Any], instance_id: int, model_name: str, run_id: str,
) -> ModelPerfRunResponse:
    get_instance_or_404(session, instance_id, claims)
    run = session.get(ModelPerfJobEntity, run_id)
    if not run or run.instance_id != instance_id or run.model_name != model_name:
        raise NotFoundError("Performance run not found")
    reconcile(session, run_id)
    session.refresh(run)
    return dto(run)


def status(
    session: Session, claims: dict[str, Any], instance_id: int, model_name: str, version: str,
) -> ModelPerfStatusResponse:
    get_instance_or_404(session, instance_id, claims)
    current = repo.active(session, instance_id, model_name)
    if current:
        reconcile(session, current.id)
    current = repo.active(session, instance_id, model_name)
    latest = repo.latest(session, instance_id, model_name, version)
    previous = commands.get_latest_perf_analyzer_run(
        RunPerfAnalyzerRequest(instance_id=instance_id, model_name=model_name, model_version=version), session, claims,
    )
    return ModelPerfStatusResponse(
        active_run=dto(current) if current else None, latest_run=dto(latest) if latest else None,
        latest_result=previous, default_image=default_image(),
    )


def stop(
    session: Session, claims: dict[str, Any], instance_id: int, model_name: str, run_id: str,
) -> ModelPerfRunResponse:
    get_instance_or_404(session, instance_id, claims)
    run = repo.lock_run(session, run_id)
    if not run or run.instance_id != instance_id or run.model_name != model_name:
        session.rollback()
        raise NotFoundError("Performance run not found")
    if run.state in repo.ACTIVE:
        run.state = "stopping"
        run.message = "Stopping benchmark; waiting for its pods to terminate."
        session.add(run)
    session.commit()
    reconcile(session, run_id)
    session.refresh(run)
    return dto(run)


def finish(session: Session, run: ModelPerfJobEntity, state: str, message: str) -> None:
    run.state, run.message, run.finished_at = state, message, datetime.utcnow()
    previous = results.get_latest_run(session, instance_id=run.instance_id, model_name=run.model_name,
                                      model_version=run.model_version)
    if previous is None:
        previous = PerfAnalyzerRunEntity(instance_id=run.instance_id, model_name=run.model_name,
                                         model_version=run.model_version)
    for name in ("batch_size", "concurrency_range", "measurement_request_count", "input_data", "command", "output"):
        setattr(previous, name, getattr(run, name))
    previous.executed_at = run.finished_at
    session.add(previous)
    session.add(run)


def reconcile(session: Session, run_id: str) -> None:
    """Keep the row lock through external operations, including resume and stop."""
    run = repo.lock_run(session, run_id, skip_locked=True)
    if run is None:
        session.rollback()
        return
    api = None
    try:
        if run.cleaned:
            session.commit()
            return
        api = Jobs()
        if run.state not in repo.ACTIVE:
            run.cleaned = api.cleanup(run)
        else:
            reconcile_active(session, run, api)
        session.add(run)
        session.commit()
    except Exception as exc:
        session.rollback()
        run = repo.lock_run(session, run_id, skip_locked=True)
        if run:
            code = str(exc.status) if isinstance(exc, ApiException) else type(exc).__name__
            run.message = f"Unable to reconcile Kubernetes resources ({code}); retrying."
            session.add(run)
            session.commit()
    finally:
        if api:
            api.close()


def reconcile_active(session: Session, run: ModelPerfJobEntity, api: Jobs) -> None:
    job, pods = api.read(run), api.pods(run)
    if run.state == "stopping":
        if not run.stop_output_saved:
            try:
                run.output = api.logs(run, pods) or run.output
            except Exception:
                run.output += "\nPartial output unavailable: could not read pod logs before stopping."
            run.stop_output_saved = True
            # Reconciliation commits this snapshot before a later pass deletes anything.
            return
        api.delete_job(run, job, pods)
        if job is None and not pods:
            finish(session, run, "cancelled", "Benchmark stopped.")
        return
    output = api.logs(run, pods)
    if output:
        run.output = output
    if run.state == "creating":
        if not run.prepared:
            if (datetime.utcnow() - run.created_at).total_seconds() > 60:
                finish(session, run, "failed", "Benchmark preparation was interrupted. Start a new run.")
            return
        if job is None:
            try:
                job = api.create(run)
            except ApiException as exc:
                if exc.status in {400, 401, 403, 404, 422}:
                    finish(session, run, "failed", f"Kubernetes rejected benchmark Job creation ({exc.status}).")
                    return
                raise
        run.job_uid = job.metadata.uid
        # Save identity before enabling execution on the next reconciliation.
        run.state, run.message = "pending", "Waiting for benchmark pod."
        return
    if job is None:
        api.delete_job(run, job, pods)
        if not pods:
            finish(session, run, "failed", "Benchmark Job disappeared; any unavailable output could not be recovered.")
        return
    if job.metadata.deletion_timestamp:
        if not pods:
            finish(session, run, "failed", "Benchmark Job was deleted externally.")
        return
    if job.spec.suspend:
        api.resume(run, job)
        return
    conditions = {c.type: c for c in job.status.conditions or [] if c.status == "True"}
    live = any(p.status.phase not in {"Succeeded", "Failed"} for p in pods)
    for pod in pods:
        for container in pod.status.container_statuses or []:
            if container.state.terminated:
                run.exit_code = container.state.terminated.exit_code
    if not live and "Complete" in conditions:
        finish(session, run, "succeeded", "Benchmark completed.")
    elif not live and "Failed" in conditions:
        condition = conditions["Failed"]
        finish(session, run, "failed", str(condition.message or condition.reason or "Benchmark failed."))
    elif (datetime.utcnow() - run.created_at).total_seconds() > deadline_seconds() + 60:
        api.delete_job(run, job, pods)
        run.message = "Benchmark exceeded its deadline; terminating its workload."
    elif any(p.status.phase == "Running" for p in pods):
        run.state, run.message = "running", "Benchmark running."
        run.started_at = run.started_at or datetime.utcnow()
    else:
        run.message = pod_message(pods)


def prepare_instance_deletion(session: Session, instance_id: int) -> None:
    instance = repo.lock_instance(session, instance_id)
    instance.perf_deleting = True
    session.add(instance)
    session.commit()
    unfinished = False
    for row in repo.for_instance(session, instance_id):
        run = repo.lock_run(session, row.id)
        if run and run.state in repo.ACTIVE:
            run.state = "stopping"
            session.add(run)
        session.commit()
        reconcile(session, row.id)
        session.refresh(row)
        unfinished |= not row.cleaned
    if unfinished:
        raise ConflictError("Performance workloads are stopping. Retry instance deletion after cleanup completes.")
    repo.delete_for_instance(session, instance_id)


def retire_legacy(session: Session) -> None:
    legacy = results.get(session)
    if not legacy:
        return
    api = None
    try:
        api = Jobs()
        if api.retire_legacy(legacy.namespace, legacy.deployment_name):
            results.delete(session, legacy)
    except Exception:
        session.rollback()
        logger.warning("Legacy Perf Analyzer retirement could not complete; retrying exact-resource cleanup.")
    finally:
        if api:
            api.close()


class PerfJobReconciler:
    def __init__(self) -> None:
        self.event = Event()
        self.thread: Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.event.clear()
        self.thread = Thread(target=self.loop, daemon=True, name="model-perf-reconciler")
        self.thread.start()

    def stop(self) -> None:
        self.event.set()
        if self.thread:
            self.thread.join(timeout=20)

    def loop(self) -> None:
        while not self.event.is_set():
            try:
                with session_factory() as session:
                    retire_legacy(session)
                    ids = repo.pending_ids(session)
                for run_id in ids:
                    if self.event.is_set():
                        break
                    with session_factory() as session:
                        reconcile(session, run_id)
            except Exception:
                logger.warning("Performance reconciliation interrupted; retrying.")
            self.event.wait(3)


reconciler = PerfJobReconciler()
