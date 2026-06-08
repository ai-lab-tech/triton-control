"""Pydantic/SQLModel schemas for OIDC provider configuration.

Defines the DTO and update-request model used by the OIDC settings API:
  ``OidcSettingsDTO``          — current OIDC settings returned by
                                  ``GET /api/auth/oidc``.
  ``UpdateOidcSettingsRequest``— request body for ``PUT /api/auth/oidc``;
                                  includes a model validator that requires all
                                  provider fields to be non-empty when
                                  ``oidc_enabled`` is ``True``.
"""

from __future__ import annotations

from typing import Any

from pydantic import field_validator, model_validator
from sqlmodel import SQLModel


class OidcSettingsDTO(SQLModel):
    oidc_enabled: bool
    issuer: str
    client_id: str
    client_secret: str
    client_secret_configured: bool = False
    redirect_uri: str
    scopes: str
    strict_discovery_document_validation: bool
    ca_certificate: str = ""
    api_base_url: str
    config_source: str = "db"
    kubernetes_enabled: bool = False


class UpdateOidcSettingsRequest(SQLModel):
    oidc_enabled: bool
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str
    strict_discovery_document_validation: bool
    ca_certificate: str = ""
    api_base_url: str

    @field_validator(
        "issuer",
        "client_id",
        "client_secret",
        "redirect_uri",
        "scopes",
        "ca_certificate",
        "api_base_url",
        mode="before",
    )
    @classmethod
    def strip_oidc_string_fields(cls, v: Any) -> str:
        return (str(v) if v else "").strip()

    @model_validator(mode="after")
    def require_fields_when_enabled(self) -> "UpdateOidcSettingsRequest":
        if self.oidc_enabled and (
            not self.issuer
            or not self.client_id
            or not self.redirect_uri
            or not self.scopes
        ):
            raise ValueError("OIDC enable requires issuer, client_id, redirect_uri, and scopes")
        return self
