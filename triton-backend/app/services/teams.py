"""Admin team use cases. Teams do not change roles or resource assignments."""

from typing import Any
from uuid import UUID

from sqlmodel import Session

from app.core.access_control import is_admin, require_admin
from app.core.identity import require_user_entity
from app.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.mappers.teams import event_to_dto, member_to_dto, team_to_dto
from app.repositories import teams, users
from app.schemas.teams import TeamDTO, TeamEventDTO, TeamMemberDTO, TeamRequest


def _admin_id(session: Session, claims: dict[str, Any]) -> int:
    require_admin(claims)
    actor = require_user_entity(session, claims)
    if not actor.is_active or not is_admin({"role": actor.role}) or actor.id is None:
        raise ForbiddenError("Active admin role required")
    return actor.id


def list_teams(session: Session, claims: dict[str, Any]) -> list[TeamDTO]:
    _admin_id(session, claims)
    return [team_to_dto(team) for team in teams.list_all(session)]


def create_team(session: Session, claims: dict[str, Any], request: TeamRequest) -> TeamDTO:
    actor = _admin_id(session, claims)
    team = teams.create(session, request.name, request.is_active)
    teams.add_event(session, team.id, actor, "team_created", request.model_dump())
    teams.commit(session)
    return team_to_dto(team)


def update_team(
    session: Session,
    claims: dict[str, Any],
    team_id: UUID,
    request: TeamRequest,
) -> TeamDTO:
    actor = _admin_id(session, claims)
    team = teams.get(session, team_id, lock=True)
    if team_id == teams.INSTALLATION_TEAM_ID and (request.name != team.name or not request.is_active):
        raise ConflictError("Manage installation engineering responsibility through user memberships")
    before = {"name": team.name, "is_active": team.is_active}
    after = request.model_dump()
    if before != after:
        team.name, team.is_active = request.name, request.is_active
        teams.add_event(session, team_id, actor, "team_updated", {"before": before, "after": after})
    teams.commit(session)
    return team_to_dto(team)


def list_members(session: Session, claims: dict[str, Any], team_id: UUID) -> list[TeamMemberDTO]:
    _admin_id(session, claims)
    teams.get(session, team_id)
    eligible = set(teams.eligible_user_ids(session, team_id))
    return [
        member_to_dto(row, user, eligible=row.user_id in eligible) for row, user in teams.list_members(session, team_id)
    ]


def set_member(
    session: Session,
    claims: dict[str, Any],
    team_id: UUID,
    user_id: int,
    *,
    active: bool,
) -> list[TeamMemberDTO]:
    actor = _admin_id(session, claims)
    team = teams.get(session, team_id, lock=True)
    user = users.find_by_id(session, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if active and (not team.is_active or not user.is_active):
        raise ConflictError("Activate the team and user before adding membership")
    membership = teams.membership(session, team_id, user_id)
    if (membership is None and active) or (membership is not None and membership.is_active != active):
        teams.set_membership(session, team_id, user_id, active)
        teams.add_event(session, team_id, actor, "member_added" if active else "member_removed", {}, user_id)
    teams.commit(session)
    return list_members(session, claims, team_id)


def list_history(
    session: Session,
    claims: dict[str, Any],
    team_id: UUID,
    limit: int = 100,
) -> list[TeamEventDTO]:
    _admin_id(session, claims)
    teams.get(session, team_id)
    return [event_to_dto(event) for event in teams.list_events(session, team_id, limit)]


def initialize_engineering_group(session: Session, claims: dict[str, Any]) -> TeamDTO:
    actor = _admin_id(session, claims)
    group = teams.initialize_engineering_group(session, actor)
    teams.commit(session)
    return team_to_dto(group)
