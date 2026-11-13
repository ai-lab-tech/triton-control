"""Admin team HTTP boundary; all policy and transitions live in services."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.teams import TeamDTO, TeamEventDTO, TeamMemberDTO, TeamRequest
from app.services import teams

router = APIRouter(prefix="/api/teams", tags=["teams"])


@router.get("", response_model=list[TeamDTO])
@translate_app_errors
def list_teams(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> list[TeamDTO]:
    return teams.list_teams(session, claims)


@router.post("", response_model=TeamDTO, status_code=201)
@translate_app_errors
def create_team(
    request: TeamRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TeamDTO:
    return teams.create_team(session, claims, request)


@router.put("/{team_id}", response_model=TeamDTO)
@translate_app_errors
def update_team(
    team_id: UUID,
    request: TeamRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TeamDTO:
    return teams.update_team(session, claims, team_id, request)


@router.get("/{team_id}/members", response_model=list[TeamMemberDTO])
@translate_app_errors
def list_members(
    team_id: UUID,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TeamMemberDTO]:
    return teams.list_members(session, claims, team_id)


@router.put("/{team_id}/members/{user_id}", response_model=list[TeamMemberDTO])
@translate_app_errors
def add_member(
    team_id: UUID,
    user_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TeamMemberDTO]:
    return teams.set_member(session, claims, team_id, user_id, active=True)


@router.delete("/{team_id}/members/{user_id}", response_model=list[TeamMemberDTO])
@translate_app_errors
def remove_member(
    team_id: UUID,
    user_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TeamMemberDTO]:
    return teams.set_member(session, claims, team_id, user_id, active=False)


@router.get("/{team_id}/events", response_model=list[TeamEventDTO])
@translate_app_errors
def list_history(
    team_id: UUID,
    limit: int = Query(default=100, ge=1, le=100),
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TeamEventDTO]:
    return teams.list_history(session, claims, team_id, limit)
