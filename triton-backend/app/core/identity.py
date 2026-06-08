"""User identity resolution from authenticated claims.

Lives in ``core/`` so that ``core/security.py`` can import it directly without
creating a ``core → services`` reverse dependency.

The only cross-cutting concern previously needed from ``services/oidc/config``
(``is_env_config_source``) is reproduced here as a private helper that reads
the same environment variable directly.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sqlmodel import Session

from app.db.entities import UserEntity
from app.exceptions import UnauthorizedError
from app.repositories import users

# ---------------------------------------------------------------------------
# Internal helper — mirrors services/oidc/config.is_env_config_source()
# without creating a core→services import.
# ---------------------------------------------------------------------------

def _is_oidc_env_config_source() -> bool:
    return (os.getenv("OIDC_CONFIG_SOURCE") or "db").strip().lower() == "env"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_pending_role(role: Optional[str]) -> bool:
    return (role or "").strip().lower() == "pending"


def require_user_entity(session: Session, claims: Dict[str, Any]) -> UserEntity:
    """Return the UserEntity that matches *claims*, or raise UnauthorizedError."""
    user = resolve_user(session, claims, auto_create_oidc=False)
    if user:
        return user
    raise UnauthorizedError("User mapping not found")


def normalize_provider(value: Optional[str]) -> str:
    provider = (value or "local").strip().lower()
    return provider if provider in {"local", "oidc"} else "local"


def oidc_admin_allowlist() -> set[str]:
    raw = os.getenv("OIDC_ADMIN_EMAILS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def has_any_admin(session: Session) -> bool:
    return users.has_any_admin(session)


def should_bootstrap_oidc_admin(session: Session, email: str) -> bool:
    if not _is_oidc_env_config_source():
        return False
    allowlist = oidc_admin_allowlist()
    if not allowlist:
        return False
    if email not in allowlist:
        return False
    return not has_any_admin(session)


def resolve_user(session: Session, claims: Dict[str, Any], auto_create_oidc: bool) -> Optional[UserEntity]:
    """Resolve a user from claims, optionally auto-creating first OIDC users."""
    provider = normalize_provider(claims.get("auth_provider"))
    email = (claims.get("email") or "").strip().lower()
    subject = (claims.get("sub") or "").strip()

    if not email:
        email = (claims.get("preferred_username") or "").strip().lower()

    user = None
    if provider == "oidc" and subject:
        user = users.find_by_oidc_subject(session, subject)

    if not user and email:
        user = users.find_by_email(session, email)

    if user and provider == "oidc" and subject and not user.oidc_subject:
        user.oidc_subject = subject
        users.save(session, user)

    if user:
        return user

    if not auto_create_oidc or provider != "oidc" or not email:
        return None

    name = (claims.get("name") or "").strip() or (email.split("@", 1)[0] if "@" in email else email)
    bootstrap_admin = should_bootstrap_oidc_admin(session, email)
    created = UserEntity(
        email=email,
        name=name,
        role="admin" if bootstrap_admin else "viewer",
        auth_provider="oidc",
        oidc_subject=subject or None,
        assigned_instances=[],
        is_active=bootstrap_admin,
    )
    return users.save(session, created)


def claims_from_user(user: UserEntity, source_claims: Dict[str, Any]) -> Dict[str, Any]:
    claims = dict(source_claims)
    claims["sub"] = user.oidc_subject or claims.get("sub") or user.email
    claims["email"] = user.email
    claims["name"] = user.name
    claims["role"] = user.role
    claims["auth_provider"] = user.auth_provider
    claims["user_id"] = user.id
    claims["access_allowed"] = bool(user.is_active and not is_pending_role(user.role))
    return claims
