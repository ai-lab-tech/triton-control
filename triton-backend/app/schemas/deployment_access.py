"""Public deployment exposure settings. Passwords are write-only."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import SecretStr, field_validator, model_validator
from sqlmodel import Field, SQLModel


class DeploymentAccessRequest(SQLModel):
    # Omitted mode preserves existing clients which select ingress by hostname.
    access_mode: Literal["internal", "ingress"] | None = None
    ingress_host: str | None = Field(default=None, description="Hostname only, e.g. smollm. No URL, port or path.")
    ingress_scheme: Literal["http", "https"] | None = None
    basic_auth_enabled: bool = False
    basic_auth_username: str | None = None
    basic_auth_password: SecretStr | None = Field(default=None, repr=False)

    @model_validator(mode="before")
    @classmethod
    def validate_admin_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if data.get("ingress_class_name") is not None:
            raise ValueError("Ingress class is configured by the administrator")
        return data

    @field_validator("ingress_host")
    @classmethod
    def validate_dns_name(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower().rstrip(".")
        if not value:
            return None
        if len(value) > 253 or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")
        ):
            raise ValueError("Enter a hostname only, without a protocol, port or path")
        return value

    @field_validator("basic_auth_username")
    @classmethod
    def validate_username(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9_.@-]{1,128}", value):
            raise ValueError("Username must contain 1–128 letters, numbers, dots, @, underscores or hyphens")
        return value

    @field_validator("basic_auth_password")
    @classmethod
    def validate_password(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            password = value.get_secret_value()
            if not 1 <= len(password.encode("utf-8")) <= 72 or "\x00" in password:
                raise ValueError("Password must contain 1–72 UTF-8 bytes and no null characters")
        return value

    @model_validator(mode="after")
    def validate_access(self) -> DeploymentAccessRequest:
        mode = self.access_mode or ("ingress" if self.ingress_host else "internal")
        if mode == "internal":
            if self.ingress_host or self.basic_auth_enabled or self.basic_auth_password or self.basic_auth_username:
                raise ValueError("Internal access cannot include an Ingress host or basic authentication")
        elif not self.ingress_host:
            raise ValueError("Ingress access requires a hostname")
        if self.basic_auth_enabled and (self.ingress_scheme or "https") != "https":
            raise ValueError("Basic authentication requires HTTPS")
        if not self.basic_auth_enabled and (self.basic_auth_password or self.basic_auth_username):
            raise ValueError("Enable basic authentication before supplying credentials")
        if bool(self.basic_auth_password) != bool(self.basic_auth_username):
            raise ValueError("Provide both username and password, or omit both to keep existing credentials")
        return self


class DeploymentAccessResponse(SQLModel):
    hostname: str | None = None
    access_mode: Literal["internal", "ingress"]
    url: str
    internal_url: str
    ingress_host: str | None = None
    ingress_scheme: Literal["http", "https"] | None = None
    ingress_class_name: str | None = None
    basic_auth_enabled: bool = False
    tls_secret_name: str | None = None
