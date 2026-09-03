"""Email delivery configuration from database or deployment environment."""

from __future__ import annotations

import os
from datetime import datetime

from sqlmodel import Session

from app.core.access_control import require_admin
from app.core.secret_encryption import decrypt_email_secret, encrypt_email_secret
from app.db.entities import EmailConfigEntity
from app.exceptions import BadRequestError
from app.repositories import account_lifecycle
from app.schemas import (
    EmailOperationResponse,
    EmailSettingsDTO,
    TestEmailRequest,
    UpdateEmailSettingsRequest,
)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def config_source() -> str:
    value = os.getenv("EMAIL_CONFIG_SOURCE", "env").strip().lower()
    return value if value in {"db", "env"} else "env"


def _env_entity() -> EmailConfigEntity:
    return EmailConfigEntity(
        id=1,
        delivery_mode=os.getenv("EMAIL_DELIVERY_MODE", "disabled").strip().lower(),
        smtp_host=os.getenv("EMAIL_SMTP_HOST", "").strip(),
        smtp_port=_int("EMAIL_SMTP_PORT", 587, 1, 65535),
        smtp_tls_mode=os.getenv("EMAIL_SMTP_TLS_MODE", "starttls").strip().lower(),
        smtp_allow_insecure=_bool("EMAIL_SMTP_ALLOW_INSECURE"),
        smtp_username=os.getenv("EMAIL_SMTP_USERNAME", "").strip(),
        smtp_password_enc=os.getenv("EMAIL_SMTP_PASSWORD", ""),
        sender_email=os.getenv("EMAIL_SENDER_EMAIL", "").strip(),
        sender_name=os.getenv("EMAIL_SENDER_NAME", "Triton Control").strip(),
        public_app_url=os.getenv("EMAIL_PUBLIC_APP_URL", "").strip().rstrip("/"),
        ca_certificate=os.getenv("EMAIL_SMTP_CA_CERTIFICATE", ""),
        invite_expiry_minutes=_int("EMAIL_INVITE_EXPIRY_MINUTES", 1440, 5, 10080),
        reset_expiry_minutes=_int("EMAIL_RESET_EXPIRY_MINUTES", 30, 5, 1440),
        connect_timeout_seconds=_int("EMAIL_CONNECT_TIMEOUT_SECONDS", 10, 1, 60),
        operation_timeout_seconds=_int("EMAIL_OPERATION_TIMEOUT_SECONDS", 15, 1, 120),
        invite_subject=os.getenv("EMAIL_INVITE_SUBJECT", "You are invited to Triton Control"),
        invite_text_template=os.getenv("EMAIL_INVITE_TEXT_TEMPLATE", ""),
        invite_html_template=os.getenv("EMAIL_INVITE_HTML_TEMPLATE", ""),
        reset_subject=os.getenv("EMAIL_RESET_SUBJECT", "Reset your Triton Control password"),
        reset_text_template=os.getenv("EMAIL_RESET_TEXT_TEMPLATE", ""),
        reset_html_template=os.getenv("EMAIL_RESET_HTML_TEMPLATE", ""),
    )


def get_runtime_config(session: Session) -> EmailConfigEntity:
    return account_lifecycle.get_email_config(session) if config_source() == "db" else _env_entity()


def smtp_password(config: EmailConfigEntity) -> str:
    if config_source() == "db":
        return decrypt_email_secret(config.smtp_password_enc)
    return config.smtp_password_enc


def _validate_runtime(config: EmailConfigEntity) -> None:
    try:
        UpdateEmailSettingsRequest(
            **{
                key: value
                for key, value in config.model_dump().items()
                if key in UpdateEmailSettingsRequest.model_fields
            }
        )
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc


def to_dto(config: EmailConfigEntity) -> EmailSettingsDTO:
    source = config_source()
    return EmailSettingsDTO(
        config_source=source,
        delivery_mode=config.delivery_mode,
        smtp_host=config.smtp_host,
        smtp_port=config.smtp_port,
        smtp_tls_mode=config.smtp_tls_mode,
        smtp_allow_insecure=config.smtp_allow_insecure,
        smtp_username=config.smtp_username,
        smtp_password_configured=bool(config.smtp_password_enc),
        sender_email=config.sender_email,
        sender_name=config.sender_name,
        public_app_url=config.public_app_url,
        ca_certificate=config.ca_certificate,
        invite_expiry_minutes=config.invite_expiry_minutes,
        reset_expiry_minutes=config.reset_expiry_minutes,
        connect_timeout_seconds=config.connect_timeout_seconds,
        operation_timeout_seconds=config.operation_timeout_seconds,
        invite_subject=config.invite_subject,
        invite_text_template=config.invite_text_template,
        invite_html_template=config.invite_html_template,
        reset_subject=config.reset_subject,
        reset_text_template=config.reset_text_template,
        reset_html_template=config.reset_html_template,
        last_status=config.last_status,
        last_status_message=config.last_status_message,
        last_status_at=config.last_status_at,
        read_only=source == "env",
        forgot_password_available=config.delivery_mode == "smtp" and bool(config.smtp_host),
    )


def get_email_settings(session: Session, claims: dict[str, object]) -> EmailSettingsDTO:
    require_admin(claims)
    return to_dto(get_runtime_config(session))


def update_email_settings(
    request: UpdateEmailSettingsRequest, session: Session, claims: dict[str, object]
) -> EmailSettingsDTO:
    require_admin(claims)
    if config_source() != "db":
        raise BadRequestError("Email settings are managed by environment configuration")
    entity = account_lifecycle.get_email_config(session)
    for key, value in request.model_dump(
        exclude={"smtp_password", "clear_smtp_password"}
    ).items():
        setattr(entity, key, value)
    if request.clear_smtp_password:
        entity.smtp_password_enc = ""  # nosec B105 - intentionally clears persisted secret
    elif request.smtp_password is not None:
        entity.smtp_password_enc = encrypt_email_secret(request.smtp_password)
    entity.updated_at = datetime.utcnow()
    _validate_runtime(entity)
    from app.services.auth.email_delivery import render_message

    for purpose, expiry in (
        ("invite", entity.invite_expiry_minutes),
        ("password_reset", entity.reset_expiry_minutes),
    ):
        render_message(
            entity,
            purpose=purpose,
            display_name="Template validation",
            link=f"{entity.public_app_url or 'https://example.invalid'}/",
            expiry_minutes=expiry,
        )
    return to_dto(account_lifecycle.save_email_config(session, entity))


def record_delivery_status(
    session: Session, config: EmailConfigEntity, status: str, message: str
) -> None:
    if config_source() != "db":
        return
    entity = account_lifecycle.get_email_config(session)
    entity.last_status = status
    entity.last_status_message = message[:500]
    entity.last_status_at = datetime.utcnow()
    account_lifecycle.save_email_config(session, entity)


def test_email(
    request: TestEmailRequest, session: Session, claims: dict[str, object]
) -> EmailOperationResponse:
    require_admin(claims)
    config = get_runtime_config(session)
    _validate_runtime(config)
    from app.services.auth.email_delivery import render_message, send_smtp

    message = render_message(
        config,
        purpose="password_reset",
        display_name="Administrator",
        link=f"{config.public_app_url.rstrip('/')}/",
        expiry_minutes=config.reset_expiry_minutes,
    )
    send_smtp(config, request.recipient, message)
    record_delivery_status(session, config, "accepted", "SMTP server accepted the test message")
    return EmailOperationResponse(
        accepted=True,
        status="accepted",
        message="SMTP server accepted the test message",
    )
