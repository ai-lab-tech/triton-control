"""Mapper from ``UserEntity`` to ``UserDTO``.

Provides a single conversion function:
  ``user_entity_to_dto(entity)`` — maps all user fields from the
    ``UserEntity`` ORM row to the outgoing ``UserDTO`` Pydantic model,
    normalising ``assigned_instances`` to an empty list when ``None`` and
    providing a safe ``id`` fallback of ``0``.
"""

from app.db.entities import UserEntity
from app.schemas import UserDTO


def user_entity_to_dto(entity: UserEntity, *, invitation_pending: bool = False) -> UserDTO:
    """Convert a user database entity to outgoing DTO."""
    account_status = (
        "active"
        if entity.is_active
        else (
            "invitation_pending" if invitation_pending else ("approval_pending" if entity.password_hash else "inactive")
        )
    )
    return UserDTO(
        id=entity.id or 0,
        email=entity.email,
        name=entity.name,
        role=entity.role,
        auth_provider=entity.auth_provider,
        oidc_subject=entity.oidc_subject,
        assigned_instances=entity.assigned_instances or [],
        is_active=entity.is_active,
        account_status=account_status,
        credential_version=entity.credential_version,
        created_at=entity.created_at,
    )
