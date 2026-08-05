"""Secure local-account invitations and password recovery."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlmodel import Session

from app.core.access_control import require_admin
from app.core.user_auth import hash_password
from app.db.entities import AccountLifecycleTokenEntity, UserEntity
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.repositories import account_lifecycle, users
from app.schemas import (
    CompleteLifecycleRequest,
    ForgotPasswordRequest,
    InviteUserRequest,
    LifecycleIssueResponse,
    NeutralRecoveryResponse,
    TokenInspectionResponse,
)
from app.services.auth.email_delivery import render_message, send_smtp
from app.services.auth.email_settings import (
    get_runtime_config,
    record_delivery_status,
)
from app.services.auth.local_auth import ensure_local_auth_allowed, validate_password

NEUTRAL_RECOVERY_MESSAGE = "If the account is eligible, password reset instructions will be sent."


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _actor(session: Session, claims: dict[str, object]) -> UserEntity | None:
    email = str(claims.get("email") or "").strip().lower()
    return users.find_by_email(session, email) if email else None


def _audit(
    session: Session,
    event_type: str,
    outcome: str,
    *,
    target_email: str = "",
    actor_email: str = "",
    purpose: str = "",
    detail: str = "",
) -> None:
    account_lifecycle.add_security_event(
        session,
        event_type=event_type,
        outcome=outcome,
        target_email=target_email,
        actor_email=actor_email,
        purpose=purpose,
        detail=detail[:500],
    )


def _revoke(session: Session, user_id: int, purpose: str | None = None) -> None:
    now = datetime.utcnow()
    for entity in account_lifecycle.list_active_tokens(session, user_id, purpose):
        entity.revoked_at = now
        account_lifecycle.save_token(session, entity)


def _issue(
    session: Session,
    user: UserEntity,
    purpose: str,
    expires_minutes: int,
    created_by_user_id: int | None,
) -> tuple[str, AccountLifecycleTokenEntity]:
    _revoke(session, user.id or 0, purpose)
    raw = secrets.token_urlsafe(48)
    entity = account_lifecycle.create_token(
        session,
        user_id=user.id,
        purpose=purpose,
        token_hash=_token_hash(raw),
        expires_at=datetime.utcnow() + timedelta(minutes=expires_minutes),
        created_by_user_id=created_by_user_id,
    )
    return raw, entity


def _valid_token(
    session: Session, raw_token: str, purpose: str
) -> tuple[AccountLifecycleTokenEntity, UserEntity] | None:
    entity = account_lifecycle.find_token_by_hash(session, _token_hash(raw_token))
    now = datetime.utcnow()
    if entity is not None and entity.expires_at <= now:
        user = users.find_by_id(session, entity.user_id)
        _audit(
            session,
            "lifecycle_token_expired",
            "expired",
            target_email=user.email if user else "",
            purpose=entity.purpose,
        )
    if (
        entity is None
        or entity.purpose != purpose
        or entity.consumed_at is not None
        or entity.revoked_at is not None
        or entity.expires_at <= now
    ):
        return None
    user = users.find_by_id(session, entity.user_id)
    return (entity, user) if user is not None else None


def _link(base_url: str, purpose: str, token: str) -> str:
    route = "activate-account" if purpose == "invite" else "reset-password"
    return f"{base_url.rstrip('/')}/{route}?token={quote(token, safe='')}"


def _deliver(
    session: Session,
    user: UserEntity,
    raw_token: str,
    entity: AccountLifecycleTokenEntity,
    purpose: str,
) -> LifecycleIssueResponse:
    config = get_runtime_config(session)
    if config.delivery_mode == "disabled":
        raise BadRequestError("Account email lifecycle is disabled")
    link = _link(config.public_app_url, purpose, raw_token)
    if config.delivery_mode == "manual-link":
        _audit(
            session,
            "lifecycle_delivery",
            "manual_link_issued",
            target_email=user.email,
            purpose=purpose,
        )
        return LifecycleIssueResponse(
            user_id=user.id or 0,
            delivery_mode="manual-link",
            expires_at=entity.expires_at,
            manual_link=link,
        )
    expiry = (
        config.invite_expiry_minutes if purpose == "invite" else config.reset_expiry_minutes
    )
    message = render_message(
        config,
        purpose=purpose,
        display_name=user.name,
        link=link,
        expiry_minutes=expiry,
    )
    try:
        send_smtp(config, user.email, message)
        record_delivery_status(session, config, "accepted", "SMTP server accepted the message")
        _audit(
            session,
            "lifecycle_delivery",
            "accepted",
            target_email=user.email,
            purpose=purpose,
        )
    except BadRequestError:
        record_delivery_status(session, config, "failed", "SMTP delivery failed")
        _audit(
            session,
            "lifecycle_delivery",
            "failed",
            target_email=user.email,
            purpose=purpose,
        )
        raise
    return LifecycleIssueResponse(
        user_id=user.id or 0,
        delivery_mode="smtp",
        expires_at=entity.expires_at,
        delivered=True,
    )


def invite_user(
    request: InviteUserRequest, session: Session, claims: dict[str, object]
) -> LifecycleIssueResponse:
    require_admin(claims)
    ensure_local_auth_allowed(session)
    if users.find_by_email(session, request.email):
        raise ConflictError("email already exists")
    role = request.role.strip().lower()
    if role not in {"admin", "member", "viewer"}:
        raise BadRequestError("role must be admin, member, or viewer")
    config = get_runtime_config(session)
    if config.delivery_mode == "disabled":
        raise BadRequestError("Account invitations are disabled")
    actor = _actor(session, claims)
    user = users.create(
        session,
        email=request.email,
        name=request.name.strip(),
        role=role,
        auth_provider="local",
        password_hash=None,
        assigned_instances=request.assigned_instances,
        is_active=False,
    )
    raw, entity = _issue(
        session,
        user,
        "invite",
        config.invite_expiry_minutes,
        actor.id if actor else None,
    )
    response = _deliver(session, user, raw, entity, "invite")
    _audit(
        session,
        "invitation_issued",
        "success",
        target_email=user.email,
        actor_email=str(claims.get("email") or ""),
        purpose="invite",
    )
    return response


def reissue_invitation(
    user_id: int, session: Session, claims: dict[str, object]
) -> LifecycleIssueResponse:
    require_admin(claims)
    ensure_local_auth_allowed(session)
    user = users.find_by_id(session, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.auth_provider != "local" or user.is_active:
        raise BadRequestError("Only inactive local accounts can be invited")
    config = get_runtime_config(session)
    actor = _actor(session, claims)
    raw, entity = _issue(
        session, user, "invite", config.invite_expiry_minutes, actor.id if actor else None
    )
    response = _deliver(session, user, raw, entity, "invite")
    _audit(
        session,
        "invitation_reissued",
        "success",
        target_email=user.email,
        actor_email=str(claims.get("email") or ""),
        purpose="invite",
    )
    return response


def revoke_invitation(user_id: int, session: Session, claims: dict[str, object]) -> dict[str, bool]:
    require_admin(claims)
    user = users.find_by_id(session, user_id)
    if user is None:
        raise NotFoundError("User not found")
    _revoke(session, user_id, "invite")
    _audit(
        session,
        "invitation_revoked",
        "success",
        target_email=user.email,
        actor_email=str(claims.get("email") or ""),
        purpose="invite",
    )
    return {"revoked": True}


def inspect_token(session: Session, raw_token: str, purpose: str) -> TokenInspectionResponse:
    found = _valid_token(session, raw_token, purpose)
    if found is None:
        return TokenInspectionResponse(valid=False)
    entity, user = found
    return TokenInspectionResponse(
        valid=True,
        purpose=purpose,
        email=user.email,
        expires_at=entity.expires_at,
    )


def activate_invitation(request: CompleteLifecycleRequest, session: Session) -> dict[str, bool]:
    validate_password(request.password)
    found = _valid_token(session, request.token, "invite")
    if found is None:
        raise BadRequestError("Invitation link is invalid or expired")
    entity, user = found
    if user.auth_provider != "local" or user.is_active:
        raise BadRequestError("Invitation link is invalid or expired")
    user.password_hash = hash_password(request.password)
    user.is_active = True
    user.credential_version += 1
    users.save(session, user)
    entity.consumed_at = datetime.utcnow()
    account_lifecycle.save_token(session, entity)
    _revoke(session, user.id or 0)
    _audit(session, "invitation_consumed", "success", target_email=user.email, purpose="invite")
    return {"activated": True}


def issue_admin_reset(
    user_id: int, session: Session, claims: dict[str, object]
) -> LifecycleIssueResponse:
    require_admin(claims)
    user = users.find_by_id(session, user_id)
    if user is None:
        raise NotFoundError("User not found")
    if user.auth_provider != "local":
        raise BadRequestError("Password recovery is managed by the OIDC provider")
    if not user.is_active:
        raise BadRequestError("Only active local accounts can reset a password")
    config = get_runtime_config(session)
    if config.delivery_mode == "disabled":
        raise BadRequestError("Password recovery is disabled")
    actor = _actor(session, claims)
    raw, entity = _issue(
        session,
        user,
        "password_reset",
        config.reset_expiry_minutes,
        actor.id if actor else None,
    )
    response = _deliver(session, user, raw, entity, "password_reset")
    _audit(
        session,
        "password_reset_issued",
        "success",
        target_email=user.email,
        actor_email=str(claims.get("email") or ""),
        purpose="password_reset",
    )
    return response


def _rate_limited(session: Session, email: str, client_key: str) -> bool:
    limit = max(1, int(os.getenv("EMAIL_RECOVERY_RATE_LIMIT", "5")))
    now = datetime.utcnow()
    window = now.replace(minute=0, second=0, microsecond=0)
    email_digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
    email_count = account_lifecycle.increment_rate_bucket(session, f"email:{email_digest}", window)
    client_digest = hashlib.sha256(client_key.encode("utf-8")).hexdigest()
    client_count = account_lifecycle.increment_rate_bucket(session, f"client:{client_digest}", window)
    return email_count > limit or client_count > limit * 5


def forgot_password(
    request: ForgotPasswordRequest, session: Session, client_key: str
) -> NeutralRecoveryResponse:
    config = get_runtime_config(session)
    if config.delivery_mode != "smtp" or not config.smtp_host:
        raise NotFoundError("Self-service password recovery is unavailable")
    if _rate_limited(session, request.email, client_key):
        _audit(session, "password_reset_requested", "throttled", purpose="password_reset")
        return NeutralRecoveryResponse(message=NEUTRAL_RECOVERY_MESSAGE)
    user = users.find_by_email(session, request.email)
    if user and user.auth_provider == "local" and user.is_active and user.password_hash:
        raw, entity = _issue(
            session, user, "password_reset", config.reset_expiry_minutes, None
        )
        try:
            _deliver(session, user, raw, entity, "password_reset")
        except BadRequestError:
            pass
    _audit(session, "password_reset_requested", "accepted", purpose="password_reset")
    return NeutralRecoveryResponse(message=NEUTRAL_RECOVERY_MESSAGE)


def complete_password_reset(
    request: CompleteLifecycleRequest, session: Session
) -> dict[str, bool]:
    validate_password(request.password)
    found = _valid_token(session, request.token, "password_reset")
    if found is None:
        raise BadRequestError("Password reset link is invalid or expired")
    entity, user = found
    if user.auth_provider != "local" or not user.is_active:
        raise BadRequestError("Password reset link is invalid or expired")
    user.password_hash = hash_password(request.password)
    user.credential_version += 1
    users.save(session, user)
    entity.consumed_at = datetime.utcnow()
    account_lifecycle.save_token(session, entity)
    _revoke(session, user.id or 0)
    _audit(
        session,
        "password_reset_consumed",
        "success",
        target_email=user.email,
        purpose="password_reset",
    )
    return {"reset": True}
