"""Business logic for the initial system-setup (bootstrap) flow.

The bootstrap flow is the first interaction a fresh deployment goes through
before any user account exists.  It is only available when OIDC is disabled.

Provides:
  ``auth_options(session)``             — returns ``{oidc_enabled: bool}``
                                           so the frontend can choose the
                                           correct login UI.
  ``bootstrap_status(session)``         — indicates whether initial setup is
                                           still pending (no users + no OIDC).
  ``bootstrap_register(request, session)``— creates the very first admin
                                           account; raises ``BadRequestError``
                                           if OIDC is enabled or users already
                                           exist.
"""

from __future__ import annotations

from sqlmodel import Session

from app.core.user_auth import hash_password
from app.exceptions import BadRequestError
from app.mappers import user_entity_to_dto
from app.repositories import users
from app.schemas import BootstrapRegisterRequest, BootstrapStatusResponse, UserDTO
from app.services.oidc.config import get_settings


def auth_options(session: Session) -> dict[str, object]:
    settings = get_settings(session)
    return {"oidc_enabled": settings.oidc_enabled}


def bootstrap_status(session: Session) -> BootstrapStatusResponse:
    is_oidc = get_settings(session).oidc_enabled
    needs_setup = (not is_oidc) and (users.count(session) == 0)
    return BootstrapStatusResponse(oidc_enabled=is_oidc, needs_setup=needs_setup)


def bootstrap_register(request: BootstrapRegisterRequest, session: Session) -> UserDTO:
    if get_settings(session).oidc_enabled:
        raise BadRequestError("OIDC is enabled")
    if users.count(session) > 0:
        raise BadRequestError("Initial setup is already complete")

    email = request.email
    name = (request.name or "").strip() or (email.split("@", 1)[0] or "Admin")
    entity = users.create(
        session,
        email=email,
        name=name,
        role="Admin",
        auth_provider="local",
        password_hash=hash_password(request.password),
        assigned_instances=[],
        is_active=True,
    )
    return user_entity_to_dto(entity)
