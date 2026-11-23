"""Inspect retired task claims; run inside the development backend with its operator identity."""

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from kubernetes import client, watch  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session, col, select

from app.db.database import session_factory
from app.db.entities import PreparationTaskEventEntity
from app.repositories import developments, preparation
from app.services.builds.contexts_storage import ContextStore, storage
from app.services.conversion_exports import ConversionExport, verify_objects
from app.services.development.workspaces import workspace_namespace
from app.services.kubernetes_client import api_client
from app.services.preparation_settings import effective


def uses_claim(pod: Any, name: str) -> bool:
    return any(
        volume.persistent_volume_claim and volume.persistent_volume_claim.claim_name == name
        for volume in pod.spec.volumes or []
    )


def attached(core: Any, apps: Any, namespace: str, workspace: str, claim: str, inspector: str | None = None) -> bool:
    try:
        apps.read_namespaced_stateful_set(workspace, namespace)
        return True
    except ApiException as exc:
        if exc.status != 404:
            raise
    for pod in core.list_namespaced_pod(namespace).items:
        if pod.metadata.uid == inspector:
            continue
        if any(
            v.persistent_volume_claim and v.persistent_volume_claim.claim_name == claim for v in pod.spec.volumes or []
        ):
            return True
    return False


def inspection(core: Any, namespace: str, claim: str, image: str, timeout: int) -> tuple[dict[str, str], str]:
    name = "claim-inspection-" + uuid4().hex
    container = {
        "name": "inspect",
        "image": image,
        "command": ["python3", "-c", Path(__file__).with_name("claim_inventory.py").read_text()],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [{"name": "data", "mountPath": "/inspection", "readOnly": True}],
    }
    pod = core.create_namespaced_pod(
        namespace,
        {
            "metadata": {"name": name},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "activeDeadlineSeconds": timeout,
                "containers": [container],
                "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim, "readOnly": True}}],
            },
        },
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = core.read_namespaced_pod(name, namespace)
            if current.metadata.uid != pod.metadata.uid or current.status.phase == "Failed":
                raise ValueError("Volume inspection failed")
            if current.status.phase == "Succeeded":
                limit = 16 * 1024**2
                response = core.read_namespaced_pod_log(name, namespace, limit_bytes=limit, _preload_content=False)
                try:
                    raw = response.read(limit + 1)
                    if len(raw) >= limit:
                        raise ValueError("Volume inventory exceeds the inspection limit")
                    return json.loads(raw), pod.metadata.uid
                finally:
                    response.close()
                    response.release_conn()
            time.sleep(2)
        raise ValueError("Volume inspection timed out")
    finally:
        core.delete_namespaced_pod(
            name, namespace, body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=pod.metadata.uid))
        )


def evaluate(
    session: Session,
    core: Any,
    apps: Any,
    namespace: str,
    claim: Any,
    installation: str,
    retention: timedelta,
    apply: bool,
) -> str:
    name, uid = claim.metadata.name, claim.metadata.uid
    if getattr(claim.metadata, "deletion_timestamp", None):
        return "Deletion already in progress"
    annotations = claim.metadata.annotations or {}
    workspace = annotations.get("triton-control.io/workspace-name")
    if (
        annotations.get("triton-control.io/installation-id") != installation
        or not workspace
        or name != f"workspace-{workspace}-0"
        or not claim.spec.volume_name
    ):
        return "Retained: ambiguous or legacy identity"
    task_id = UUID(annotations["triton-control.io/task-id"])

    def active(inspector: str | None = None) -> bool:
        return developments.find_by_workload(session, namespace, workspace) is not None or attached(
            core, apps, namespace, workspace, name, inspector
        )

    if active():
        return "Retained: active or reattached workspace"
    events = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.task_id == task_id,
            col(PreparationTaskEventEntity.action).in_(
                [
                    "workspace_deleted",
                    "conversion_export_verified",
                    "conversion_export_started",
                    "conversion_export_interrupted",
                ]
            ),
        )
    ).all()
    relevant = [
        e for e in events if e.changes.get("workspace") == workspace and e.changes.get("namespace") == namespace
    ]
    retired = [e for e in relevant if e.action == "workspace_deleted"]
    receipts = [
        e.changes for e in relevant if e.action == "conversion_export_verified" and e.changes.get("claim_uid") == uid
    ]
    latest_verified: dict[str, int] = {}
    for event in relevant:
        if event.action == "conversion_export_verified" and event.changes.get("claim_uid") == uid:
            directory = event.changes["directory"]
            latest_verified[directory] = max(event.revision, latest_verified.get(directory, 0))
    if any(
        e.action in {"conversion_export_started", "conversion_export_interrupted"}
        and latest_verified.get(e.changes["directory"], 0) < e.revision
        for e in relevant
    ):
        return "Retained: a conversion export is incomplete"
    if len(retired) != 1 or not receipts:
        return "Retained: missing retirement record or verified conversion"
    if retired[0].created_at.replace(tzinfo=timezone.utc) + retention > datetime.now(timezone.utc):
        return "Retained: within retention period"
    limits = effective(session)
    saved: dict[str, set[str]] = {}
    for receipt in receipts:
        request = ConversionExport.model_validate(receipt)
        with storage(session, receipt) as store:
            if not isinstance(store, ContextStore):
                raise ValueError("Expected versioned S3 storage")
            verify_objects(store, request, receipt["prefix"], time.monotonic() + limits.transfer_timeout_seconds)
        for obj in request.objects:
            relative = obj.key[len(receipt["prefix"]) + 1 :].split("/", 1)[1]
            key = (Path("conversion") / request.directory / relative).as_posix()
            saved.setdefault(key, set()).add(obj.sha256)
    before = core.list_namespaced_pod(namespace)
    if any(uses_claim(pod, name) for pod in before.items):
        return "Retained: claim reattached before inspection"
    files, inspector = inspection(
        core,
        namespace,
        name,
        os.getenv("TASK_WORKSPACE_STAGING_IMAGE", "python:3.12-alpine"),
        limits.transfer_timeout_seconds,
    )
    # Only this exact generated editor descriptor can be discarded without an export.
    descriptor = json.dumps(
        {
            "folders": [
                {"path": "/workspace/conversion", "name": "Conversion (writable)"},
                {"path": "/inputs/verified", "name": "Verified inputs (read-only)"},
            ]
        }
    )
    descriptor_key = ".triton-control/workspace.code-workspace"
    if files.get(descriptor_key) == hashlib.sha256(descriptor.encode()).hexdigest():
        files.pop(descriptor_key)
    if any(digest not in saved.get(key, set()) for key, digest in files.items()):
        return "Retained: volume contains unexported or changed files"
    session.expire_all()
    current = core.read_namespaced_persistent_volume_claim(name, namespace)
    if (
        current.metadata.uid != uid
        or current.metadata.annotations != claim.metadata.annotations
        or current.spec.volume_name != claim.spec.volume_name
        or active(inspector)
    ):
        return "Retained: volume identity or attachment changed"
    # Detect even short-lived writers that disappeared during the disk scan.
    stream = watch.Watch()
    try:
        for pod_event in stream.stream(
            core.list_namespaced_pod, namespace, resource_version=before.metadata.resource_version, timeout_seconds=2
        ):
            pod = pod_event["object"]
            if pod.metadata.uid != inspector and uses_claim(pod, name):
                return "Retained: another pod attached during inspection"
    finally:
        stream.stop()
    if not apply:
        return "Eligible"
    task = preparation.get(session, task_id, lock=True)
    prior = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.task_id == task_id,
            PreparationTaskEventEntity.action == "retired_claim_deletion_requested",
            col(PreparationTaskEventEntity.changes)["claim_uid"].as_string() == uid,
        )
    ).first()
    if prior is None:
        task.revision += 1
        preparation.add_event(
            session,
            task,
            0,
            "retired_claim_deletion_requested",
            {
                "claim_uid": uid,
                "claim_name": name,
                "volume": current.spec.volume_name,
                "namespace": namespace,
                "installation": installation,
                "initiator": "retention operator",
            },
        )
    session.commit()
    current = core.read_namespaced_persistent_volume_claim(name, namespace)
    if (
        current.metadata.uid != uid
        or current.spec.volume_name != claim.spec.volume_name
        or current.metadata.annotations != annotations
        or active(inspector)
    ):
        return "Retained: claim changed before deletion"
    core.delete_namespaced_persistent_volume_claim(
        name,
        namespace,
        body=client.V1DeleteOptions(
            preconditions=client.V1Preconditions(uid=uid, resource_version=current.metadata.resource_version)
        ),
    )
    return f"Deletion requested for persistent volume {current.spec.volume_name}"


def report_volumes(session: Session, core: Any, namespace: str, installation: str, apply: bool) -> None:
    attempts = session.exec(
        select(PreparationTaskEventEntity).where(
            PreparationTaskEventEntity.action == "retired_claim_deletion_requested",
        )
    ).all()
    for attempt in attempts:
        record = attempt.changes
        if record.get("namespace") != namespace or record.get("installation") != installation:
            continue
        volume = record["volume"]
        outcome = None
        try:
            pv = core.read_persistent_volume(volume)
        except ApiException as exc:
            if exc.status == 404:
                outcome = "Reclaimed"
            elif exc.status == 403:
                print(volume, "PV status unavailable: requires get on persistentvolumes")
            else:
                raise
        else:
            if pv.spec.claim_ref and pv.spec.claim_ref.uid == record["claim_uid"]:
                outcome = f"{pv.status.phase} (reclaim policy {pv.spec.persistent_volume_reclaim_policy})"
        if outcome:
            print(volume, outcome)
            if apply:
                task = preparation.get(session, attempt.task_id, lock=True)
                previous = session.exec(
                    select(PreparationTaskEventEntity).where(
                        PreparationTaskEventEntity.task_id == attempt.task_id,
                        PreparationTaskEventEntity.action == "retired_claim_outcome",
                        col(PreparationTaskEventEntity.changes)["claim_uid"].as_string() == record["claim_uid"],
                        col(PreparationTaskEventEntity.changes)["outcome"].as_string() == outcome,
                    )
                ).first()
                if not previous:
                    task.revision += 1
                    preparation.add_event(session, task, 0, "retired_claim_outcome", {**record, "outcome": outcome})
                session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    installation = os.getenv("TRITON_INSTALLATION_ID") or os.getenv("TASK_WORKSPACE_INSTALLATION_ID")
    if not installation or os.getenv("TRITON_CONTROL_MODE", "development") != "development":
        parser.error("A development installation identity is required")
    days = int(os.getenv("TASK_WORKSPACE_RETENTION_DAYS", "30"))
    if days < 0:
        parser.error("Retention days must be nonnegative")
    namespace = workspace_namespace()
    api = api_client()
    core, apps = client.CoreV1Api(api), client.AppsV1Api(api)
    failed = False
    for claim in core.list_namespaced_persistent_volume_claim(namespace).items:
        if claim.metadata.name.startswith("workspace-task-"):
            with session_factory() as session:
                try:
                    result = evaluate(
                        session, core, apps, namespace, claim, installation, timedelta(days=days), args.apply
                    )
                except Exception as exc:
                    failed = True
                    result = "Inspection incomplete: " + type(exc).__name__
                print(claim.metadata.name, result)
    with session_factory() as session:
        report_volumes(session, core, namespace, installation, args.apply)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
