"""Durable, UID-fenced validation using a dedicated Kubernetes namespace."""

import os
from contextlib import closing
from datetime import timedelta
from typing import Any, Callable
from uuid import UUID

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from pydantic import TypeAdapter, ValidationError
from sqlmodel import Session

from app.core.canonical import digest
from app.db.entities import OperationEntity, UserEntity
from app.exceptions import ForbiddenError, UnprocessableEntityError
from app.repositories import preparation, teams
from app.schemas.operations import ReconcileResult
from app.schemas.serving_metadata import Name
from app.schemas.validation import ValidationManifest, ValidationReport
from app.services.candidate_validation.workload import Inputs, render
from app.services.contract_io import MetadataError
from app.services.kubernetes_client import api_client
from app.services.operations import Action, ExecutionContext
from app.services.validation import check_report, document_digest, load_validation

TIMEOUT = 5
MAX_REPORT = 128 * 1024
OWNER = "triton-control/validation-operation"


def optional(read: Callable[..., Any], *args: str) -> Any:
    try:
        return read(*args, _request_timeout=TIMEOUT)
    except ApiException as exc:
        if exc.status != 404:
            raise
        return None


def owned(metadata: Any, uid: str, kind: str) -> bool:
    return any(ref.controller and ref.uid == uid and ref.kind == kind for ref in metadata.owner_references or [])


def read_result(core: Any, job: Any, namespace: str, manifest: ValidationManifest) -> ValidationReport:
    pods = core.list_namespaced_pod(
        namespace,
        label_selector=f"batch.kubernetes.io/controller-uid={job.metadata.uid}",
        limit=2,
        _request_timeout=TIMEOUT,
    )
    if len(pods.items) != 1:
        raise ValueError("Ambiguous runner")
    pod = pods.items[0]
    if not owned(pod.metadata, job.metadata.uid, "Job") or pod.status.phase != "Succeeded":
        raise ValueError("Unverified runner")
    spec = pod.spec
    images = {c.name: c.image for c in spec.init_containers or []}
    if (
        not manifest.repository.materializer_image
        or images.get("verify-inputs") != manifest.repository.materializer_image
        or images.get("triton") != manifest.image
        or images.get("release-readiness") != manifest.repository.materializer_image
        or images.get("materialize-models") != manifest.repository.materializer_image
        or len(spec.containers) != 1
        or spec.containers[0].image != manifest.evaluator.image
    ):
        raise ValueError("Runner images differ from manifest")
    if any(item.restart_count for item in pod.status.init_container_statuses or []):
        raise ValueError("Candidate restarted during validation")
    statuses = pod.status.container_statuses or []
    if (
        len(statuses) != 1
        or statuses[0].name != "evaluator"
        or statuses[0].restart_count
        or not statuses[0].state.terminated
        or statuses[0].state.terminated.exit_code != 0
    ):
        raise ValueError("Evaluator did not finish")
    with closing(
        core.read_namespaced_pod_log(
            pod.metadata.name,
            namespace,
            container="evaluator",
            tail_lines=1,
            limit_bytes=MAX_REPORT + 1,
            _preload_content=False,
            _request_timeout=TIMEOUT,
        )
    ) as stream:
        raw = stream.read(MAX_REPORT + 1)
    current = core.read_namespaced_pod(pod.metadata.name, namespace, _request_timeout=TIMEOUT)
    if current.metadata.uid != pod.metadata.uid or len(raw) > MAX_REPORT:
        raise ValueError("Invalid report source or size")
    report = load_validation(raw, format="json").document
    if not isinstance(report, ValidationReport):
        raise ValueError("Expected a validation report")
    # Kubelet timestamps have second precision. Both clocks describe this pod.
    finished = statuses[0].state.terminated
    tolerance = timedelta(seconds=1)
    if (
        not finished.started_at
        or not finished.finished_at
        or report.started_at < finished.started_at - tolerance
        or report.completed_at > finished.finished_at + tolerance
    ):
        raise MetadataError("report-outside-runner-lifetime")
    check_report(manifest, report)
    return report


def reconcile(operation: OperationEntity, context: ExecutionContext, core: Any, batch: Any) -> ReconcileResult:
    result = dict(operation.result)
    if not result:
        if operation.cancel_requested:
            return ReconcileResult(state="cancelled")
        namespace = os.getenv("TRITON_VALIDATION_NAMESPACE", "")
        TypeAdapter(Name).validate_python(namespace)
        return ReconcileResult(
            result={
                "namespace": namespace,
                "manifestDigest": document_digest(ValidationManifest.model_validate(operation.payload)),
            },
            retry_after=1,
        )
    ns = result["namespace"]
    name = "validate-" + UUID(operation.id).hex
    manifest = ValidationManifest.model_validate(operation.payload)
    job = optional(batch.read_namespaced_job, name, ns)
    uid = result.get("jobUID")
    if job and ((job.metadata.annotations or {}).get(OWNER) != operation.id or (uid and job.metadata.uid != uid)):
        return ReconcileResult(
            state="cancelled" if operation.cancel_requested else "failed",
            result=result,
            message="Validation Job identity conflict",
        )
    if job and not uid:
        if (
            not result.get("specDigest")
            or not job.spec.suspend
            or (job.metadata.annotations or {}).get("triton-control/spec") != result["specDigest"]
        ):
            raise ValueError("Unrecorded Job cannot supply a result")
        result["jobUID"] = job.metadata.uid
        return ReconcileResult(result=result, retry_after=1)
    if operation.cancel_requested or "finishedState" in result:
        if job and uid:
            context.checkpoint()
            batch.delete_namespaced_job(
                name,
                ns,
                body={"propagationPolicy": "Foreground", "preconditions": {"uid": job.metadata.uid}},
                _request_timeout=TIMEOUT,
            )
            return ReconcileResult(result=result, message="Removing validation workload")
        return ReconcileResult(
            state="cancelled" if operation.cancel_requested else result["finishedState"],
            result=result,
            message=result.get("outcome", ""),
        )
    if not job and uid:
        raise ValueError("Owned Job disappeared")
    if job and not job.spec.suspend:
        conditions = {c.type for c in job.status.conditions or [] if c.status == "True"}
        if "Failed" in conditions:
            result.update(finishedState="failed", outcome="Validation Job failed or exceeded deadline")
            return ReconcileResult(result=result, retry_after=1)
        if "Complete" in conditions:
            report = read_result(core, job, ns, manifest)
            result.update(report=report.model_dump(mode="json", by_alias=True), reportDigest=document_digest(report))
            result.update(finishedState="succeeded" if report.status == "passed" else "failed", outcome=report.status)
            return ReconcileResult(result=result, retry_after=1)
        return ReconcileResult(result=result)
    approved = optional(core.read_namespaced_config_map, "validation-inputs-" + UUID(operation.target).hex, ns)
    if not approved or not approved.immutable:
        raise ValueError("Immutable validation input binding required")
    inputs = Inputs.model_validate_json((approved.data or {}).get("inputs.json", ""))
    config, definition = render(manifest, inputs, name, ns)
    spec_digest = digest(definition)
    definition["metadata"]["annotations"] = {
        OWNER: operation.id,
        "triton-control/spec": spec_digest,
        "triton-control/input-binding": approved.metadata.uid,
    }
    if "specDigest" not in result:
        result.update(specDigest=spec_digest, bindingUID=approved.metadata.uid)
        return ReconcileResult(result=result, retry_after=1)
    if result["specDigest"] != spec_digest or result["bindingUID"] != approved.metadata.uid:
        raise ValueError("Authorized inputs changed before launch")
    context.checkpoint()
    if not job:
        created = batch.create_namespaced_job(ns, definition, _request_timeout=TIMEOUT)
        result["jobUID"] = created.metadata.uid
        return ReconcileResult(result=result, retry_after=1)
    if any((job.metadata.annotations or {}).get(k) != v for k, v in definition["metadata"]["annotations"].items()):
        raise ValueError("Suspended validation Job binding changed")
    uid = job.metadata.uid
    config["metadata"]["ownerReferences"] = [
        {"apiVersion": "batch/v1", "kind": "Job", "name": name, "uid": uid, "controller": True}
    ]
    existing = optional(core.read_namespaced_config_map, config["metadata"]["name"], ns)
    if existing is None:
        core.create_namespaced_config_map(ns, config, _request_timeout=TIMEOUT)
    elif not existing.immutable or not owned(existing.metadata, uid, "Job") or existing.data != config["data"]:
        raise ValueError("Materialization config identity conflict")
    context.checkpoint()
    batch.patch_namespaced_job(
        name,
        ns,
        {"metadata": {"resourceVersion": job.metadata.resource_version, "uid": uid}, "spec": {"suspend": False}},
        _request_timeout=TIMEOUT,
    )
    return ReconcileResult(result=result)


class ValidationAdapter:
    cleanup_after_authorized_cancel = True

    def normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ValidationManifest.model_validate(payload).model_dump(mode="json", by_alias=True)
        except ValidationError:
            raise UnprocessableEntityError("Invalid validation manifest") from None

    def authorize(self, session: Session, actor: UserEntity, target: str, action: Action) -> None:
        if action == "submit" and not os.getenv("TRITON_VALIDATION_NAMESPACE"):
            raise UnprocessableEntityError("Validation namespace is not configured")
        try:
            task_id = UUID(target)
            if str(task_id) != target:
                raise ValueError("Expected a canonical task ID")
            task = preparation.get(session, task_id)
        except ValueError:
            raise UnprocessableEntityError("Expected a preparation task ID") from None
        if actor.role == "admin":
            return
        if actor.id != task.assignee_user_id or actor.id not in teams.eligible_user_ids(session, task.assigned_team_id):
            raise ForbiddenError("Assigned ML engineer required")
        if action in {"submit", "retry", "execute"} and task.state != "claimed":
            raise ForbiddenError("Claim the preparation task before validation")

    def prepare_retry(self, session: Session, row: OperationEntity) -> None:
        raise UnprocessableEntityError(
            "Submit a new validation attempt with a new Idempotency-Key to retain this report"
        )

    def reconcile(self, operation: OperationEntity, context: ExecutionContext) -> ReconcileResult:
        if operation.cancel_requested and not operation.result:
            return ReconcileResult(state="cancelled")
        try:
            with api_client() as api:
                return reconcile(operation, context, client.CoreV1Api(api), client.BatchV1Api(api))
        except (ValueError, ApiException) as exc:
            if isinstance(exc, ApiException):
                if exc.status not in {400, 403, 422} or "finishedState" in operation.result:
                    raise
                code = f"kubernetes-rejected-{exc.status}"
            else:
                code = exc.code if isinstance(exc, MetadataError) else "invalid-input-or-report"
            result = {**operation.result, "errorCode": code}
            if result.get("jobUID") and "finishedState" not in result:
                result.update(finishedState="failed", outcome="Validation input, workload or report rejected")
                return ReconcileResult(result=result, retry_after=1)
            return ReconcileResult(
                state="failed", result=result, message="Validation input, workload or report rejected"
            )
