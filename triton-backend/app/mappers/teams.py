"""Pure team entity-to-response mapping."""

from app.db.entities import TeamEntity, TeamEventEntity, UserEntity, UserTeamEntity
from app.schemas.teams import TeamDTO, TeamEventDTO, TeamMemberDTO


def team_to_dto(team: TeamEntity) -> TeamDTO:
    return TeamDTO(id=team.id, name=team.name, is_active=team.is_active, created_at=team.created_at)


def member_to_dto(membership: UserTeamEntity, user: UserEntity, *, eligible: bool) -> TeamMemberDTO:
    return TeamMemberDTO(
        user_id=membership.user_id,
        name=user.name,
        email=user.email,
        role=user.role,
        is_active=membership.is_active,
        user_is_active=user.is_active,
        eligible=eligible,
        updated_at=membership.updated_at,
    )


def event_to_dto(event: TeamEventEntity) -> TeamEventDTO:
    if event.id is None:
        raise ValueError("Team history requires a persisted event")
    return TeamEventDTO(
        id=event.id,
        actor_user_id=event.actor_user_id,
        subject_user_id=event.subject_user_id,
        action=event.action,
        changes=event.changes,
        created_at=event.created_at,
    )
