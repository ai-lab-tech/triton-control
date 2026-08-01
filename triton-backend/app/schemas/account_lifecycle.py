"""DTOs for optional local-account invitation, recovery, and email delivery."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel

from app.schemas.user import NormalizedEmail, validate_password_policy

DeliveryMode = Literal["disabled", "manual-link", "smtp"]
EmailConfigSource = Literal["db", "env"]
SmtpTlsMode = Literal["starttls", "tls", "none"]
TokenPurpose = Literal["invite", "password_reset"]
AccountCreationMode = Literal["password", "invite", "inactive"]


def _absolute_http_url(value: str) -> str:
    normalized = (value or "").strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("public_app_url must be an absolute HTTP(S) URL")
    return normalized


class InviteUserRequest(SQLModel):
    email: NormalizedEmail
    name: str
    role: str = "viewer"
    assigned_instances: List[str] = Field(default_factory=list)


class LifecycleIssueResponse(SQLModel):
    user_id: int
    delivery_mode: DeliveryMode
    expires_at: datetime
    delivered: bool = False
    manual_link: Optional[str] = None


class TokenInspectionResponse(SQLModel):
    valid: bool
    purpose: Optional[TokenPurpose] = None
    email: Optional[str] = None
    expires_at: Optional[datetime] = None


class CompleteLifecycleRequest(SQLModel):
    token: str = Field(min_length=20, max_length=1024)
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_policy(value) or value


class TokenInspectionRequest(SQLModel):
    token: str = Field(min_length=20, max_length=1024)


class ForgotPasswordRequest(SQLModel):
    email: NormalizedEmail


class NeutralRecoveryResponse(SQLModel):
    message: str = "If the account is eligible, password reset instructions will be sent."


class AdminResetRequest(SQLModel):
    user_id: int


class EmailSettingsDTO(SQLModel):
    config_source: EmailConfigSource
    delivery_mode: DeliveryMode = "disabled"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_tls_mode: SmtpTlsMode = "starttls"
    smtp_allow_insecure: bool = False
    smtp_username: str = ""
    smtp_password_configured: bool = False
    sender_email: str = ""
    sender_name: str = "Triton Control"
    public_app_url: str = ""
    ca_certificate: str = ""
    invite_expiry_minutes: int = 1440
    reset_expiry_minutes: int = 30
    connect_timeout_seconds: int = 10
    operation_timeout_seconds: int = 15
    invite_subject: str = "You are invited to Triton Control"
    invite_text_template: str = ""
    invite_html_template: str = ""
    reset_subject: str = "Reset your Triton Control password"
    reset_text_template: str = ""
    reset_html_template: str = ""
    last_status: str = "not_tested"
    last_status_message: str = ""
    last_status_at: Optional[datetime] = None
    read_only: bool = False
    forgot_password_available: bool = False


class UpdateEmailSettingsRequest(SQLModel):
    delivery_mode: DeliveryMode = "disabled"
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_tls_mode: SmtpTlsMode = "starttls"
    smtp_allow_insecure: bool = False
    smtp_username: str = ""
    smtp_password: Optional[str] = Field(default=None, max_length=4096)
    clear_smtp_password: bool = False
    sender_email: str = ""
    sender_name: str = "Triton Control"
    public_app_url: str = ""
    ca_certificate: str = ""
    invite_expiry_minutes: int = Field(default=1440, ge=5, le=10080)
    reset_expiry_minutes: int = Field(default=30, ge=5, le=1440)
    connect_timeout_seconds: int = Field(default=10, ge=1, le=60)
    operation_timeout_seconds: int = Field(default=15, ge=1, le=120)
    invite_subject: str = Field(default="You are invited to Triton Control", min_length=1, max_length=255)
    invite_text_template: str = ""
    invite_html_template: str = ""
    reset_subject: str = Field(default="Reset your Triton Control password", min_length=1, max_length=255)
    reset_text_template: str = ""
    reset_html_template: str = ""

    @field_validator("public_app_url")
    @classmethod
    def validate_public_url(cls, value: str) -> str:
        return _absolute_http_url(value)

    @model_validator(mode="after")
    def validate_transport(self) -> "UpdateEmailSettingsRequest":
        if self.delivery_mode in {"manual-link", "smtp"} and not self.public_app_url:
            raise ValueError("public_app_url is required for manual-link and smtp delivery")
        if self.delivery_mode == "smtp":
            if not self.smtp_host.strip() or not self.sender_email.strip():
                raise ValueError("smtp_host and sender_email are required for smtp delivery")
            if self.smtp_tls_mode == "none" and not self.smtp_allow_insecure:
                raise ValueError("plain SMTP requires smtp_allow_insecure=true")
        return self


class TestEmailRequest(SQLModel):
    recipient: NormalizedEmail


class EmailOperationResponse(SQLModel):
    accepted: bool
    status: str
    message: str

