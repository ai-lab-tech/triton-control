"""Team persistence, row serialization and current routing eligibility queries."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.db.entities import TeamEntity, TeamEventEntity, UserEntity, UserTeamEntity
from app.exceptions import ConflictError, NotFoundError


def list_all(session: Session) -> list[TeamEntity]:
    return list(session.exec(select(TeamEntity).order_by(TeamEntity.name)).all())


def get(session: Session, team_id: UUID, *, lock: bool = False) -> TeamEntity:
    statement = select(TeamEntity).where(TeamEntity.id == team_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    team = session.exec(statement).first()
    if team is None:
        raise NotFoundError("Team not found")
    return team


def create(session: Session, name: str, is_active: bool) -> TeamEntity:
    team = TeamEntity(name=name, is_active=is_active)
    session.add(team)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Team name already exists") from exc
    return team


def membership(session: Session, team_id: UUID, user_id: int) -> UserTeamEntity | None:
    return session.get(UserTeamEntity, (team_id, user_id))


def set_membership(session: Session, team_id: UUID, user_id: int, active: bool) -> None:
    row = membership(session, team_id, user_id)
    if row is None:
        row = UserTeamEntity(team_id=team_id, user_id=user_id)
    row.is_active = active
    row.updated_at = datetime.utcnow()
    session.add(row)


def list_members(session: Session, team_id: UUID) -> list[tuple[UserTeamEntity, UserEntity]]:
    statement = (
        select(UserTeamEntity, UserEntity)
        .join(UserEntity, col(UserEntity.id) == col(UserTeamEntity.user_id))
        .where(UserTeamEntity.team_id == team_id)
        .order_by(UserEntity.name, col(UserEntity.id))
    )
    return [(membership, user) for membership, user in session.exec(statement).all()]


def eligible_user_ids(session: Session, team_id: UUID) -> list[int]:
    """Fresh routing candidates only; never a grant of resource permission."""
    statement = (
        select(UserTeamEntity.user_id)
        .join(TeamEntity, col(TeamEntity.id) == col(UserTeamEntity.team_id))
        .join(UserEntity, col(UserEntity.id) == col(UserTeamEntity.user_id))
        .where(
            UserTeamEntity.team_id == team_id,
            col(UserTeamEntity.is_active).is_(True),
            col(TeamEntity.is_active).is_(True),
            col(UserEntity.is_active).is_(True),
            func.lower(func.trim(col(UserEntity.role))).in_(["member", "admin"]),
        )
        .order_by(col(UserTeamEntity.user_id))
    )
    return list(session.exec(statement).all())


def add_event(
    session: Session,
    team_id: UUID,
    actor_id: int,
    action: str,
    changes: dict[str, Any],
    subject_id: int | None = None,
) -> None:
    session.add(
        TeamEventEntity(
            team_id=team_id,
            actor_user_id=actor_id,
            subject_user_id=subject_id,
            action=action,
            changes=changes,
        )
    )


def list_events(session: Session, team_id: UUID, limit: int) -> list[TeamEventEntity]:
    return list(
        session.exec(
            select(TeamEventEntity)
            .where(TeamEventEntity.team_id == team_id)
            .order_by(col(TeamEventEntity.id).desc())
            .limit(limit)
        ).all()
    )


def commit(session: Session) -> None:
    """Persist the mutation and its history together; constraints arbitrate races."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Team name or membership conflicts with an existing record") from exc


INSTALLATION_TEAM_ID = UUID("552af89b-09a5-48b6-9fc8-3f01afc8f0d8")


def initialize_engineering_group(session: Session, actor_id: int) -> TeamEntity:
    """Create the installation group once; preserve legacy teams and their audit trail."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(741293817)"))
    existing = session.get(TeamEntity, INSTALLATION_TEAM_ID)
    if existing is not None:
        return existing
    sources = list_all(session)
    name = "engineering"
    if any(team.name == name for team in sources):
        name = str(INSTALLATION_TEAM_ID)
    group = TeamEntity(id=INSTALLATION_TEAM_ID, name=name, is_active=True)
    session.add(group)
    session.flush()
    add_event(session, group.id, actor_id, "team_created", {"installation_group": True})
    imported: set[int] = set()
    for source in sources:
        if not source.is_active:
            continue
        for membership_row, user in list_members(session, source.id):
            if membership_row.is_active and user.id is not None and user.id not in imported:
                set_membership(session, group.id, user.id, True)
                add_event(session, group.id, actor_id, "member_added", {"source_team_id": str(source.id)}, user.id)
                imported.add(user.id)
    return group
