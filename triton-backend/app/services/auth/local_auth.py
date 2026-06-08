"""Business logic for local email/password authentication and user registration.

Provides use cases for the local (non-OIDC) auth strategy:
  ``login(request, session)``                    — verify credentials and
                                                    return a signed JWT.
  ``register_user(request, session, claims)``    — admin-only user creation.
  ``self_register(request, session)``            — public self-registration;
                                                    accounts are auto-approved
                                                    or placed in a pending
                                                    state depending on
                                                    configuration.
  ``validate_password(password)``                — enforce minimum password
                                                    requirements.
  ``ensure_local_auth_allowed(session)``         — raises ``BadRequestError``
                                                    when OIDC is enabled and
                                                    local logins are disabled.
  ``has_active_local_admin_login(session)``       — guard used before disabling
                                                    OIDC to ensure at least one
                                                    local admin exists.
"""

from __future__ import annotations

import os
from typing import Any

from sqlmodel import Session

from app.core.access_control import require_admin
from app.core.identity import is_pending_role
from app.core.user_auth import hash_password, issue_access_token, verify_password
from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    UnauthorizedError,
)
from app.mappers import user_entity_to_dto
from app.repositories import users
from app.schemas import (
    PASSWORD_RULE_DESCRIPTION,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    SelfRegisterRequest,
    UserDTO,
    validate_password_policy,
)
from app.services.oidc.config import get_settings


def _parse_access_token_expiry_minutes() -> int:
    raw = os.getenv("JWT_ACCESS_TOKEN_EXPIRES_MINUTES")
    if raw is None:
        return 60
    try:
        value = int(raw)
    except ValueError:
        return 60
    return value if value > 0 else 60


def validate_password(password: str | None, *, required: bool = True) -> None:
    try:
        validate_password_policy(password, required=required)
    except ValueError as exc:
        raise BadRequestError(PASSWORD_RULE_DESCRIPTION) from exc


def oidc_enabled(session: Session) -> bool:
    return get_settings(session).oidc_enabled


def user_count(session: Session) -> int:
    return users.count(session)


def ensure_local_auth_allowed(session: Session) -> None:
    if oidc_enabled(session):
        raise BadRequestError(
            "Local email/password authentication is disabled while OIDC is enabled",
        )


def has_active_local_admin_login(session: Session) -> bool:
    return users.has_active_local_admin_login(session)


def login(request: LoginRequest, session: Session) -> LoginResponse:
    ensure_local_auth_allowed(session)
    if not oidc_enabled(session) and user_count(session) == 0:
        email = request.email
        validate_password(request.password)

        display_name = email.split("@", 1)[0] or "Admin"
        users.create(
            session,
            email=email,
            name=display_name,
            role="Admin",
            auth_provider="local",
            password_hash=hash_password(request.password),
            assigned_instances=[],
            is_active=True,
        )

    email = request.email
    user = users.find_by_email(session, email)
    if not user:
        raise UnauthorizedError("Invalid credentials")

    if user.auth_provider != "local":
        raise BadRequestError("Use OIDC login for this user")

    if not user.password_hash:
        raise ForbiddenError("Account is not registered yet")

    if not verify_password(request.password, user.password_hash):
        raise UnauthorizedError("Invalid credentials")

    if not user.is_active and not is_pending_role(user.role):
        raise ForbiddenError("Account pending admin approval")

    token = issue_access_token(
        {
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "auth_provider": user.auth_provider,
        },
        expires_minutes=_parse_access_token_expiry_minutes(),
    )
    return LoginResponse(access_token=token, user=user_entity_to_dto(user))


def self_register(request: SelfRegisterRequest, session: Session) -> UserDTO:
    ensure_local_auth_allowed(session)
    email = request.email

    existing = users.find_by_email(session, email)
    if existing:
        if existing.auth_provider != "local":
            raise ConflictError("User is configured for OIDC login")
        has_empty_password_placeholder = bool(
            existing.password_hash and verify_password("", existing.password_hash)
        )
        if existing.password_hash and not has_empty_password_placeholder:
            raise ConflictError("User is already registered")

        existing.password_hash = hash_password(request.password)
        if (request.name or "").strip():
            existing.name = (request.name or "").strip()
        existing.is_active = True
        return user_entity_to_dto(users.save(session, existing))

    name = (request.name or "").strip() or (email.split("@", 1)[0] or "User")
    created = users.create(
        session,
        email=email,
        name=name,
        role="viewer",
        auth_provider="local",
        password_hash=hash_password(request.password),
        assigned_instances=[],
        is_active=False,
    )
    return user_entity_to_dto(created)


def register_user(
    request: CreateUserRequest,
    session: Session,
    claims: dict[str, Any],
) -> UserDTO:
    require_admin(claims)
    if oidc_enabled(session) and request.auth_provider == "local":
        raise BadRequestError("Cannot create local users while OIDC is enabled")

    email = request.email
    provider = request.auth_provider
    raw_password = request.password or ""
    normalized_password = raw_password if raw_password else None

    if provider == "oidc" and not oidc_enabled(session):
        raise BadRequestError("OIDC is disabled")

    existing = users.find_by_email(session, email)
    if existing:
        raise ConflictError("email already exists")

    entity = users.create(
        session,
        email=email,
        name=request.name,
        role=request.role,
        auth_provider=provider,
        password_hash=(
            hash_password(normalized_password)
            if provider == "local" and normalized_password
            else None
        ),
        oidc_subject=(request.oidc_subject or "").strip() or None,
        assigned_instances=request.assigned_instances,
        is_active=True,
    )
    return user_entity_to_dto(entity)
