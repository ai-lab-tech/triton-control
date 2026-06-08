"""Pydantic/SQLModel schemas for user management and authentication.

Defines DTOs and request models used by the auth and user management APIs:
  ``UserDTO``                  — outgoing user representation.
  ``CreateUserRequest``        — admin-only user creation body.
  ``SelfRegisterRequest``      — public self-registration body.
  ``BootstrapRegisterRequest`` — first-admin registration during setup.
  ``LoginRequest`` /
  ``LoginResponse``            — local email/password auth.
  ``UpdateUserRoleRequest``    — admin role change body.
  ``UpdateUserInstancesRequest``— admin instance assignment body.
  ``BootstrapStatusResponse``  — indicates whether initial setup is needed.

Helpers:
  ``NormalizedEmail`` — annotated type that strips whitespace, lowercases,
                         and validates the presence of ``@``.
  ``ROLE_ALIASES``    — maps legacy display names (e.g. ``ml engineer``) to
                         canonical internal role strings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, List, Optional

from pydantic import AfterValidator, StringConstraints, field_validator, model_validator
from sqlmodel import Field, SQLModel

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
PASSWORD_RULE_DESCRIPTION = (
    "password must be 12-128 characters, include uppercase, lowercase, digit, and special character, "
    "and must not contain whitespace"
)  # nosec B105
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ROLE_ALIASES: dict[str, str] = {
    "admin": "admin",
    "ml engineer": "member",
    "sre": "member",
    "user": "member",
    "member": "member",
    "viewer": "viewer",
}


def _check_email(v: str) -> str:
    if not EMAIL_PATTERN.fullmatch(v):
        raise ValueError("Valid email is required")
    return v


def validate_password_policy(password: str | None, *, required: bool = True) -> str | None:
    if password is None or password == "":  # nosec B105
        if required:
            raise ValueError(PASSWORD_RULE_DESCRIPTION)
        return None
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    if any(char.isspace() for char in password):
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    if not any(char.islower() for char in password):
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    if not any(char.isupper() for char in password):
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    if not any(char.isdigit() for char in password):
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    if not any(not char.isalnum() for char in password):
        raise ValueError(PASSWORD_RULE_DESCRIPTION)
    return password


NormalizedEmail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_lower=True),
    AfterValidator(_check_email),
]


class UserDTO(SQLModel):
    id: int
    email: str
    name: str
    role: str
    auth_provider: str
    oidc_subject: Optional[str] = None
    assigned_instances: List[str]
    is_active: bool
    created_at: datetime


class CreateUserRequest(SQLModel):
    email: NormalizedEmail
    name: str
    role: str = "viewer"
    auth_provider: str = "local"
    password: Optional[str] = None
    oidc_subject: Optional[str] = None
    assigned_instances: List[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("name is required")
        return v

    @field_validator("auth_provider")
    @classmethod
    def validate_auth_provider(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in {"local", "oidc"}:
            raise ValueError("auth_provider must be local or oidc")
        return v

    @field_validator("role")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        normalized = ROLE_ALIASES.get((v or "").strip().lower(), "")
        if not normalized:
            raise ValueError("role must be admin, member, or viewer")
        return normalized

    @model_validator(mode="after")
    def validate_password_if_local(self) -> "CreateUserRequest":
        if self.auth_provider == "local":
            validate_password_policy(self.password, required=False)
        return self


class LoginRequest(SQLModel):
    email: NormalizedEmail
    password: str


class SelfRegisterRequest(SQLModel):
    email: NormalizedEmail
    password: str
    name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_policy(v) or v


class LoginResponse(SQLModel):
    access_token: str
    token_type: str = "bearer"
    user: UserDTO


class BootstrapStatusResponse(SQLModel):
    oidc_enabled: bool
    needs_setup: bool


class BootstrapRegisterRequest(SQLModel):
    email: NormalizedEmail
    password: str
    name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return validate_password_policy(v) or v


class UpdateUserInstancesRequest(SQLModel):
    assigned_instances: List[str] = Field(default_factory=list)


class UpdateUserRoleRequest(SQLModel):
    role: str

    @field_validator("role")
    @classmethod
    def normalize_role(cls, v: str) -> str:
        normalized = ROLE_ALIASES.get((v or "").strip().lower(), "")
        if not normalized:
            raise ValueError("role must be admin, member, or viewer")
        return normalized
