"""Admin-only business logic for user management.

All functions call ``require_admin(claims)`` before performing any work,
so they raise ``ForbiddenError`` immediately for non-admin callers.

Provides:
  ``list_users(session, claims)``                          — all users as DTOs.
  ``delete_user(session, claims, user_id)``                — remove a user by id.
  ``update_user_instances(session, claims, user_id, req)`` — replace the set of
    Triton instances assigned to a user.
  ``update_user_role(session, claims, user_id, req)``      — change a user's role.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import NotFoundError
from app.mappers import user_entity_to_dto
from app.repositories import users
from app.schemas import UpdateUserInstancesRequest, UpdateUserRoleRequest, UserDTO


def get_user_or_404(session: Session, user_id: int) -> Any:
    row = users.find_by_id(session, user_id)
    if not row:
        raise NotFoundError("User not found")
    return row


def list_users(session: Session, claims: dict[str, Any]) -> list[UserDTO]:
    require_admin(claims)
    return [user_entity_to_dto(row) for row in users.list_all(session)]


def delete_user(session: Session, claims: dict[str, Any], user_id: int) -> None:
    require_admin(claims)
    users.delete(session, get_user_or_404(session, user_id))


def update_user_instances(
    session: Session,
    claims: dict[str, Any],
    user_id: int,
    request: UpdateUserInstancesRequest,
) -> UserDTO:
    require_admin(claims)
    row = get_user_or_404(session, user_id)
    row.assigned_instances = request.assigned_instances
    users.save(session, row)
    return user_entity_to_dto(row)


def update_user_role(
    session: Session,
    claims: dict[str, Any],
    user_id: int,
    request: UpdateUserRoleRequest,
) -> UserDTO:
    require_admin(claims)
    row = get_user_or_404(session, user_id)
    row.role = request.role
    row.is_active = True
    users.save(session, row)
    return user_entity_to_dto(row)
