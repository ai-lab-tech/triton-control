from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core import installation_policy
from app.core.access_control import require_admin
from app.db.entities import AccessGroupEntity
from app.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from app.repositories import access_groups
from app.schemas.access_groups import AccessGroupDTO, AccessGroupInstanceOption, AccessGroupRequest, AccessGroupUpdate


def _require_admin(claims: dict[str, Any]) -> None:
    if installation_policy.mode() != "production":
        raise ForbiddenError("Access groups are available only in production mode")
    installation_policy.require("identity_admin")
    require_admin(claims)


def list_groups(session: Session, claims: dict[str, Any]) -> list[AccessGroupDTO]:
    _require_admin(claims)
    return [
        AccessGroupDTO(
            id=group.id, name=group.name, revision=group.revision,
            user_ids=access_groups.member_ids(session, group.id),
            instance_ids=access_groups.instance_ids(session, group.id),
        )
        for group in access_groups.list_all(session)
    ]


def list_instance_options(session: Session, claims: dict[str, Any]) -> list[AccessGroupInstanceOption]:
    _require_admin(claims)
    return [
        AccessGroupInstanceOption(id=instance_id, name=name)
        for instance_id, name in access_groups.list_instance_options(session)
    ]


def _current_group(session: Session, group_id: UUID, revision: int) -> AccessGroupEntity:
    group = access_groups.lock(session, group_id)
    if group is None:
        raise NotFoundError("Access group not found")
    if group.revision != revision:
        raise ConflictError("Access group changed. Reload it before editing.")
    return group


def _save(session: Session, group: AccessGroupEntity, request: AccessGroupRequest) -> AccessGroupDTO:
    if not access_groups.targets_exist(session, request.user_ids, request.instance_ids):
        raise NotFoundError("A selected user or instance no longer exists")
    added_users = request.user_ids - access_groups.member_ids(session, group.id)
    if added_users and not access_groups.all_viewers(session, added_users):
        raise UnprocessableEntityError("Only viewers can be added to production access groups")
    group.name = request.name
    session.add(group)
    result = AccessGroupDTO(
        id=group.id, name=group.name, revision=group.revision,
        user_ids=request.user_ids, instance_ids=request.instance_ids,
    )
    try:
        session.flush()
        access_groups.replace_grants(session, group.id, request.user_ids, request.instance_ids)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError("Group name already exists or a selected user or instance changed") from exc
    return result


def create_group(session: Session, claims: dict[str, Any], request: AccessGroupRequest) -> AccessGroupDTO:
    _require_admin(claims)
    return _save(session, AccessGroupEntity(name=request.name), request)


def update_group(
    session: Session, claims: dict[str, Any], group_id: UUID, request: AccessGroupUpdate,
) -> AccessGroupDTO:
    _require_admin(claims)
    group = _current_group(session, group_id, request.revision)
    group.revision += 1
    return _save(session, group, request)


def delete_group(session: Session, claims: dict[str, Any], group_id: UUID, revision: int) -> None:
    _require_admin(claims)
    group = _current_group(session, group_id, revision)
    access_groups.clear_grants(session, group_id)
    session.delete(group)
    session.commit()
