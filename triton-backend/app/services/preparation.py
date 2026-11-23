"""Authorized, revision-checked engineering task use cases.

Task state is independent of build/deployment state. Creation and attempt recording
are internal integration points, not replacement handoff or execution endpoints.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.core import installation_policy
from app.core.identity import require_user_entity
from app.db.entities import HandoffEntity, PreparationTaskEntity
from app.exceptions import ConflictError, ForbiddenError
from app.mappers.preparation import attempt_to_dto, event_to_dto, task_to_dto
from app.repositories import handoffs as handoff_repository
from app.repositories import preparation as repository
from app.repositories import teams
from app.schemas.handoffs import missing_evaluation_inputs
from app.schemas.lineage import EngineeringAttempt
from app.schemas.preparation import (
    AttemptDTO,
    InformationRequest,
    ReasonRequest,
    ReassignRequest,
    ResolveInputsRequest,
    TaskCreate,
    TaskDTO,
    TaskEventDTO,
    TaskRevision,
)


def _actor(session: Session, claims: dict[str, Any]) -> tuple[int, bool, str]:
    mapped = require_user_entity(session, claims)
    if mapped.id is None:
        raise ForbiddenError("Active account required")
    user = repository.current_user(session, mapped.id)
    if user is None or not user.is_active:
        raise ForbiddenError("Active account required")
    role = user.role.strip().lower()
    if role not in {"admin", "member", "viewer"}:
        raise ForbiddenError("Approved account role required")
    return mapped.id, role == "admin", role


def _blocked(task: PreparationTaskEntity, eligible: set[int], optional_inputs: set[str]) -> list[str]:
    reasons = ["missing_input:" + name for name in task.missing_inputs if name not in optional_inputs]
    if not eligible:
        reasons.append("no_eligible_engineers")
    if task.assignee_user_id is not None and task.assignee_user_id not in eligible:
        reasons.append("assignee_ineligible")
    if task.state == "cancelled":
        reasons.append("cancelled")
    return reasons


def _dto(session: Session, task_id: UUID) -> TaskDTO:
    task = repository.get(session, task_id)
    eligible = set(teams.eligible_user_ids(session, task.assigned_team_id))
    handoff = next(iter(handoff_repository.for_tasks(session, [task_id])), None)
    return _task_dto(task, eligible, handoff)


def _handoff_missing(handoff: HandoffEntity | None) -> list[str]:
    if handoff is None:
        return []
    payload = handoff.payload
    request = payload["request"]
    return missing_evaluation_inputs(
        payload["dataset"], payload["baseline"], request["expected_behavior"], request["acceptance_criteria"],
    )


def _task_dto(task: PreparationTaskEntity, eligible: set[int], handoff: HandoffEntity | None) -> TaskDTO:
    optional_inputs = set(_handoff_missing(handoff))
    missing = sorted(set(task.missing_inputs) | optional_inputs)
    blocked = _blocked(task, eligible, optional_inputs)
    return task_to_dto(task, blocked).model_copy(update={"missing_inputs": missing})


def authorize_task_access(
    session: Session, claims: dict[str, Any], task_id: UUID, *, lock: bool = False,
) -> tuple[int, bool, bool]:
    """Check current task visibility and eligibility, optionally locking task and team."""
    task = repository.get(session, task_id, lock=lock)
    if lock:
        # Membership writes also lock this team row. Recheck eligibility after acquiring it.
        teams.get(session, task.assigned_team_id, lock=True)
    actor, admin, role = _actor(session, claims)
    if lock and role not in {"admin", "member"}:
        raise ForbiddenError("Member or admin role required")
    eligible = actor in teams.eligible_user_ids(session, task.assigned_team_id)
    if not (admin or eligible or actor == task.submitter_user_id):
        raise ForbiddenError("Preparation task access denied")
    return actor, admin, eligible


def list_tasks(session: Session, claims: dict[str, Any], limit: int = 100, offset: int = 0, *,
               view: str | None = None, search: str = "") -> list[TaskDTO]:
    actor, admin, _ = _actor(session, claims)
    team_ids = [] if admin else list(repository.eligible_members(session, actor_id=actor))
    rows = repository.queue(session, actor, team_ids, admin=admin, limit=limit, offset=offset, view=view, search=search)
    eligible = repository.eligible_members(session, {row.assigned_team_id for row in rows})
    handoffs = {row.task_id: row for row in handoff_repository.for_tasks(session, [row.id for row in rows])}
    return [_task_dto(row, eligible.get(row.assigned_team_id, set()), handoffs.get(row.id)) for row in rows]


def get_task(session: Session, claims: dict[str, Any], task_id: UUID) -> TaskDTO:
    authorize_task_access(session, claims, task_id)
    return _dto(session, task_id)


def list_events(
    session: Session, claims: dict[str, Any], task_id: UUID, limit: int = 100, offset: int = 0
) -> list[TaskEventDTO]:
    authorize_task_access(session, claims, task_id)
    return [event_to_dto(row) for row in repository.events(session, task_id, limit, offset)]


def list_attempts(
    session: Session, claims: dict[str, Any], task_id: UUID, limit: int = 100, offset: int = 0
) -> list[AttemptDTO]:
    authorize_task_access(session, claims, task_id)
    return [attempt_to_dto(row) for row in repository.attempts(session, task_id, limit, offset)]


def create_task(session: Session, claims: dict[str, Any], request: TaskCreate, *, commit: bool = True) -> TaskDTO:
    """Called by a future validated handoff service; commit=False joins its transaction.

    No resource content or access is granted here. The caller must authorize and
    capture any source snapshot before passing its immutable identifier.
    """
    installation_policy.require("development_jobs")
    teams.get(session, teams.INSTALLATION_TEAM_ID, lock=True)
    actor, _, role = _actor(session, claims)
    if role not in {"admin", "member"}:
        raise ForbiddenError("Member or admin role required")
    task = repository.add_task(
        session,
        title=request.title,
        submitter_user_id=actor,
        assigned_team_id=teams.INSTALLATION_TEAM_ID,
        source_snapshot_id=request.source_snapshot_id,
        missing_inputs=sorted(set(request.missing_inputs)),
    )
    repository.add_event(
        session,
        task,
        actor,
        "created",
        {
            "assigned_team_id": str(task.assigned_team_id),
            "missing_inputs": task.missing_inputs,
            "source_snapshot_id": str(task.source_snapshot_id) if task.source_snapshot_id else None,
        },
    )
    repository.persist(session, commit=commit)
    return _dto(session, task.id)


def _edit(session: Session, claims: dict[str, Any], task_id: UUID, request: TaskRevision) -> tuple[int, bool, bool]:
    installation_policy.require("development_jobs")
    actor, admin, eligible = authorize_task_access(session, claims, task_id, lock=True)
    task = repository.get(session, task_id)
    if task.revision != request.expected_revision:
        raise ConflictError("Preparation task changed; reload its current revision")
    if task.state == "cancelled":
        raise ConflictError("Cancelled preparation tasks cannot be changed")
    return actor, admin, eligible


def _finish(
    session: Session,
    task_id: UUID,
    actor: int,
    action: str,
    before: dict[str, Any],
    reason: str | None = None,
    *,
    commit: bool = True,
    details: dict[str, Any] | None = None,
) -> TaskDTO:
    task = repository.get(session, task_id)
    task.revision += 1
    task.updated_at = datetime.utcnow()
    after = _values(session, task_id)
    changes: dict[str, Any] = {"before": before, "after": after}
    if details is not None:
        changes.update(details)
    if reason is not None:
        changes["reason"] = reason
    repository.add_event(session, task, actor, action, changes)
    repository.persist(session, commit=commit)
    return _dto(session, task_id)


def _values(session: Session, task_id: UUID) -> dict[str, Any]:
    task = repository.get(session, task_id)
    return {"state": task.state, "assignee_user_id": task.assignee_user_id, "missing_inputs": list(task.missing_inputs)}


def claim(session: Session, claims: dict[str, Any], task_id: UUID, request: TaskRevision) -> TaskDTO:
    actor, _, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    if not eligible:
        raise ForbiddenError("Current engineering membership required to claim")
    if task.assignee_user_id is not None:
        raise ConflictError("Preparation task is already assigned")
    before = _values(session, task_id)
    task.assignee_user_id = actor
    task.state = "claimed"
    return _finish(session, task_id, actor, "claimed", before)


def release_claim(session: Session, claims: dict[str, Any], task_id: UUID, request: TaskRevision) -> TaskDTO:
    actor, admin, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    if not admin and not (eligible and task.assignee_user_id == actor):
        raise ForbiddenError("Only the current assignee or an admin may release the claim")
    if task.assignee_user_id is None:
        raise ConflictError("Preparation task has no claim")
    before = _values(session, task_id)
    task.assignee_user_id = None
    task.state = "awaiting_information" if task.state == "awaiting_information" else "queued"
    return _finish(session, task_id, actor, "claim_released", before)


def _engineer_or_admin(admin: bool, eligible: bool, assignee: int | None, actor: int) -> None:
    if not admin and not (eligible and assignee == actor):
        raise ForbiddenError("Current assignee or admin required")


def request_information(
    session: Session, claims: dict[str, Any], task_id: UUID, request: InformationRequest
) -> TaskDTO:
    actor, admin, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    _engineer_or_admin(admin, eligible, task.assignee_user_id, actor)
    before = _values(session, task_id)
    missing = sorted(set(task.missing_inputs) | set(request.missing_inputs))
    if len(missing) > 50:
        raise ConflictError("A preparation task supports at most 50 missing inputs")
    task.missing_inputs = missing
    task.state = "awaiting_information"
    return _finish(session, task_id, actor, "information_requested", before, request.reason)


def resolve_inputs(session: Session, claims: dict[str, Any], task_id: UUID, request: ResolveInputsRequest) -> TaskDTO:
    actor, admin, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    _engineer_or_admin(admin, eligible, task.assignee_user_id, actor)
    handoff = next(iter(handoff_repository.for_tasks(session, [task_id])), None)
    if set(request.resolved_inputs) & set(_handoff_missing(handoff)):
        raise ConflictError("Handoff evaluation inputs are still absent; submit a new revision with the missing inputs")
    if not set(request.resolved_inputs) <= set(task.missing_inputs):
        raise ConflictError("Resolved inputs must name currently missing inputs")
    before = _values(session, task_id)
    task.missing_inputs = sorted(set(task.missing_inputs) - set(request.resolved_inputs))
    if not task.missing_inputs and task.state == "awaiting_information":
        task.state = "claimed" if task.assignee_user_id is not None else "queued"
    # This records preparation completeness only, never validation/release approval.
    return _finish(session, task_id, actor, "inputs_resolved", before, request.reason)


def cancel(session: Session, claims: dict[str, Any], task_id: UUID, request: ReasonRequest) -> TaskDTO:
    actor, admin, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    if not (admin or actor == task.submitter_user_id or (eligible and task.assignee_user_id == actor)):
        raise ForbiddenError("Submitter, current assignee or admin required")
    before = _values(session, task_id)
    task.state = "cancelled"
    task.assignee_user_id = None
    return _finish(session, task_id, actor, "cancelled", before, request.reason)


def reassign(session: Session, claims: dict[str, Any], task_id: UUID, request: ReassignRequest) -> TaskDTO:
    actor, admin, _ = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    if not admin:
        raise ForbiddenError("Admin role required for reassignment")
    if request.assignee_user_id is not None and request.assignee_user_id not in teams.eligible_user_ids(
        session, task.assigned_team_id
    ):
        raise ConflictError("New assignee must be a current eligible engineer")
    if task.assignee_user_id == request.assignee_user_id:
        raise ConflictError("Preparation task already has this assignment")
    before = _values(session, task_id)
    task.assignee_user_id = request.assignee_user_id
    if task.state != "awaiting_information":
        task.state = "claimed" if task.assignee_user_id is not None else "queued"
    return _finish(session, task_id, actor, "reassigned", before, request.reason)


def record_attempt(
    session: Session,
    claims: dict[str, Any],
    task_id: UUID,
    request: TaskRevision,
    attempt: EngineeringAttempt,
    *,
    commit: bool = True,
) -> TaskDTO:
    """Internal hook: retain an actual completed attempt with #25 identities."""
    installation_policy.require("development_jobs")
    actor, admin, eligible = _edit(session, claims, task_id, request)
    task = repository.get(session, task_id)
    _engineer_or_admin(admin, eligible, task.assignee_user_id, actor)
    before = _values(session, task_id)
    repository.add_attempt(session, task_id, actor, attempt.model_dump(mode="json"))
    return _finish(
        session,
        task_id,
        actor,
        "attempt_recorded",
        before,
        commit=commit,
        details={"attempt_origin": attempt.origin.model_dump()},
    )
