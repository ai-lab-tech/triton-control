from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from app.db.entities import (
    AccessGroupEntity,
    AccessGroupInstanceEntity,
    AccessGroupMemberEntity,
    TritonInstanceEntity,
    UserEntity,
)


def list_all(session: Session) -> Sequence[AccessGroupEntity]:
    return session.exec(select(AccessGroupEntity).order_by(AccessGroupEntity.name)).all()


def list_instance_options(session: Session) -> list[tuple[int, str]]:
    rows = session.exec(select(TritonInstanceEntity.id, TritonInstanceEntity.name)
                       .order_by(TritonInstanceEntity.name)).all()
    return [(instance_id, name) for instance_id, name in rows if instance_id is not None]


def lock(session: Session, group_id: UUID) -> AccessGroupEntity | None:
    return session.exec(
        select(AccessGroupEntity).where(AccessGroupEntity.id == group_id).with_for_update()
    ).one_or_none()


def member_ids(session: Session, group_id: UUID) -> set[int]:
    return set(session.exec(
        select(AccessGroupMemberEntity.user_id).where(AccessGroupMemberEntity.group_id == group_id)
    ).all())


def instance_ids(session: Session, group_id: UUID) -> set[int]:
    return set(session.exec(
        select(AccessGroupInstanceEntity.instance_id).where(AccessGroupInstanceEntity.group_id == group_id)
    ).all())


def granted_instance_names(session: Session, user_id: int) -> list[str]:
    return list(session.exec(
        select(TritonInstanceEntity.name)
        .join(AccessGroupInstanceEntity)
        .join(AccessGroupEntity)
        .join(AccessGroupMemberEntity)
        .where(AccessGroupMemberEntity.user_id == user_id)
        .distinct()
        .order_by(TritonInstanceEntity.name)
    ).all())


def targets_exist(session: Session, user_ids: set[int], instance_ids: set[int]) -> bool:
    users = session.exec(select(UserEntity.id).where(col(UserEntity.id).in_(user_ids))).all()
    instances = session.exec(
        select(TritonInstanceEntity.id).where(col(TritonInstanceEntity.id).in_(instance_ids))
    ).all()
    return set(users) == user_ids and set(instances) == instance_ids


def all_viewers(session: Session, user_ids: set[int]) -> bool:
    viewers = session.exec(
        select(UserEntity.id).where(
            col(UserEntity.id).in_(user_ids), func.lower(func.trim(UserEntity.role)) == "viewer",
        )
    ).all()
    return set(viewers) == user_ids


def clear_grants(session: Session, group_id: UUID) -> None:
    session.execute(delete(AccessGroupMemberEntity).where(col(AccessGroupMemberEntity.group_id) == group_id))
    session.execute(delete(AccessGroupInstanceEntity).where(col(AccessGroupInstanceEntity.group_id) == group_id))


def replace_grants(session: Session, group_id: UUID, user_ids: set[int], instance_ids: set[int]) -> None:
    clear_grants(session, group_id)
    session.add_all(AccessGroupMemberEntity(group_id=group_id, user_id=user_id) for user_id in user_ids)
    session.add_all(
        AccessGroupInstanceEntity(group_id=group_id, instance_id=instance_id) for instance_id in instance_ids
    )
