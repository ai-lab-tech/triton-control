"""Compose authorized task summaries without provider calls or resource access grants."""
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.core import installation_policy
from app.repositories import conversion_exports, handoffs
from app.repositories import engineering_queue as repository
from app.repositories import preparation as tasks
from app.schemas.engineering_queue import (
    CandidateSummary,
    ConversionSummary,
    QueueEntry,
    TaskActions,
    TaskOverview,
    TaskSource,
)
from app.schemas.preparation import TaskDTO
from app.services import preparation


def _entries(session: Session, claims: dict[str, Any], rows: list[TaskDTO]) -> list[QueueEntry]:
    actor, admin, role = preparation._actor(session, claims)
    eligible = tasks.eligible_members(session, {row.assigned_team_id for row in rows})
    receipts = {row.task_id: row for row in handoffs.for_tasks(session, [row.id for row in rows])}
    snapshots = repository.snapshots(session, [row.source_snapshot_id for row in rows if row.source_snapshot_id])
    user_ids = {row.submitter_user_id for row in rows} | {row.assignee_user_id for row in rows if row.assignee_user_id}
    users, teams = repository.names(session, user_ids, {row.assigned_team_id for row in rows})
    result = []
    for task in rows:
        source = TaskSource()
        snapshot = snapshots.get(task.source_snapshot_id) if task.source_snapshot_id else None
        handoff = receipts.get(task.id)
        if snapshot:
            manifest = snapshot.manifest
            source = TaskSource(kind="experiment", revision=str(snapshot.id), snapshot_id=snapshot.id,
                                instance_id=snapshot.instance_id,
                                models=[model["name"] for model in manifest["request"]["models"]])
        elif handoff:
            request = handoff.payload["request"]
            source = TaskSource(kind=request["source"]["kind"], revision=request["revision"], handoff_id=handoff.id)
        member = actor in eligible.get(task.assigned_team_id, set()) and role in {"admin", "member"}
        assigned = member and task.assignee_user_id == actor
        mutable = task.state != "cancelled" and role in {"admin", "member"}
        result.append(QueueEntry(
            task=task, source=source, team_name=teams.get(task.assigned_team_id, str(task.assigned_team_id)),
            submitter_name=users.get(task.submitter_user_id, str(task.submitter_user_id)),
            assignee_name=(users.get(task.assignee_user_id, str(task.assignee_user_id))
                           if task.assignee_user_id else None),
            actions=TaskActions(claim=mutable and member and task.assignee_user_id is None,
                                release=mutable and task.assignee_user_id is not None and (assigned or admin),
                                request_information=mutable and (assigned or admin),
                                open_workspace=(mutable and assigned
                                                and (snapshot is not None or not task.has_blocking_inputs))),
        ))
    return result


def queue(session: Session, claims: dict[str, Any], limit: int = 50, offset: int = 0, *,
          view: str | None = None, search: str = "") -> list[QueueEntry]:
    installation_policy.require("development_jobs")
    return _entries(session, claims, preparation.list_tasks(session, claims, limit, offset, view=view, search=search))


def overview(session: Session, claims: dict[str, Any], task_id: UUID) -> TaskOverview:
    installation_policy.require("development_jobs")
    task = preparation.get_task(session, claims, task_id)
    entry = _entries(session, claims, [task])[0]
    candidates = [CandidateSummary(installation_id=row.installation_id, record_id=row.record_id,
                                   candidate_digest=row.candidate_digest, created_at=row.created_at)
                  for row in repository.candidates(session, task_id)]
    conversions = [ConversionSummary.model_validate(row) for row in conversion_exports.statuses(session, task_id)]
    return TaskOverview(**entry.model_dump(), candidates=candidates, conversions=conversions)
