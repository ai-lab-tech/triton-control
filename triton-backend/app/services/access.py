"""Shared instance-access guards used across multiple service sub-modules.

Provides two helpers that combine repository lookups with access-policy
enforcement so individual service modules do not duplicate the same checks:
  ``ensure_instance_access(session, claims, instance_name)`` — raises
    ``ForbiddenError`` when a non-admin caller has neither a direct nor a
    group assignment for ``instance_name``. Admins always pass through.
  ``get_instance_or_404(session, instance_id, claims)``      — fetches a
    ``TritonInstanceEntity`` by primary key, raises ``NotFoundError`` if
    absent, and optionally calls ``ensure_instance_access`` when ``claims``
    is supplied.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core import installation_policy
from app.core.access_control import is_admin
from app.core.identity import require_user_entity
from app.db.entities import TritonInstanceEntity, UserEntity
from app.exceptions import ForbiddenError, NotFoundError
from app.repositories import access_groups, instances


def effective_instance_names(session: Session, user: UserEntity) -> list[str]:
    direct = [name for name in (user.assigned_instances or []) if name]
    if installation_policy.mode() != "production" or user.role.strip().lower() != "viewer" or user.id is None:
        return direct
    inherited = access_groups.granted_instance_names(session, user.id)
    return list(dict.fromkeys([*direct, *inherited]))


def ensure_instance_access(session: Session, claims: dict[str, Any], instance_name: str) -> None:
    """Raise ForbiddenError when a non-admin user has no access to *instance_name*."""
    if is_admin(claims):
        return
    user = require_user_entity(session, claims)
    if instance_name not in effective_instance_names(session, user):
        raise ForbiddenError("Instance access denied")


def get_instance_or_404(
    session: Session,
    instance_id: int,
    claims: dict[str, Any] | None = None,
) -> TritonInstanceEntity:
    """Fetch a Triton instance and optionally enforce per-user access."""
    instance = instances.find_by_id(session, instance_id)
    if not instance:
        raise NotFoundError("Instance not found")
    if claims is not None:
        ensure_instance_access(session, claims, instance.name)
    return instance
