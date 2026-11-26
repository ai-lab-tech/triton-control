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
from app.exceptions import ConflictError, NotFoundError
from app.mappers import user_entity_to_dto
from app.repositories import account_lifecycle, users
from app.schemas import UpdateUserInstancesRequest, UpdateUserRoleRequest, UserDTO


def get_user_or_404(session: Session, user_id: int) -> Any:
    row = users.find_by_id(session, user_id)
    if not row:
        raise NotFoundError("User not found")
    return row


def list_users(session: Session, claims: dict[str, Any]) -> list[UserDTO]:
    require_admin(claims)
    pending_invitation_user_ids = account_lifecycle.list_user_ids_with_pending_invitations(session)
    return [
        user_entity_to_dto(
            row,
            invitation_pending=row.id in pending_invitation_user_ids,
        )
        for row in users.list_all(session)
    ]


def _protect_last_admin(session: Session, user_id: int) -> None:
    if users.is_last_active_admin_for_update(session, user_id):
        raise ConflictError("At least one active administrator must remain.")


def delete_user(session: Session, claims: dict[str, Any], user_id: int) -> None:
    require_admin(claims)
    _protect_last_admin(session, user_id)
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
    if request.role != "admin":
        _protect_last_admin(session, user_id)
    row = get_user_or_404(session, user_id)
    row.role = request.role
    row.is_active = row.auth_provider != "local" or bool(row.password_hash)
    users.save(session, row)
    return user_entity_to_dto(row)
