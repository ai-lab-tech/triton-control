"""OIDC settings access layer.

Abstracts the *source* of OIDC configuration so the rest of the application
is decoupled from whether settings come from the database or from environment
variables:
  ``OIDC_CONFIG_SOURCE=db``  (default) — settings are read from / written to
                              the ``oidc_config`` table via the repository.
  ``OIDC_CONFIG_SOURCE=env`` — settings are derived from environment variables
                              and treated as read-only.

Public API:
  ``get_settings``           — Return the current ``OidcSettingsDTO``.
  ``update_settings``        — Persist a new ``OidcSettingsDTO`` (DB source only).
  ``default_settings``       — Build an ``OidcSettingsDTO`` from env vars.
  ``is_env_config_source``   — ``True`` when the env-var source is active.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlmodel import Session

from app.db.entities import OidcConfigEntity
from app.exceptions import BadRequestError
from app.mappers import oidc_entity_to_dto
from app.repositories import oidc_config
from app.schemas import OidcSettingsDTO, UpdateOidcSettingsRequest


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def oidc_config_source() -> str:
    source = (os.getenv("OIDC_CONFIG_SOURCE") or "db").strip().lower()
    return "env" if source == "env" else "db"


def is_env_config_source() -> bool:
    return oidc_config_source() == "env"


def kubernetes_enabled() -> bool:
    # Optional explicit override for external/runtime setups.
    env_override = os.getenv("KUBERNETES_ENABLED")
    if env_override is not None:
        return env_override.strip().lower() in {"1", "true", "yes", "on", "y"}

    # Default runtime autodetection: backend is considered "kubernetes-enabled"
    # when it runs inside a Kubernetes pod with in-cluster credentials.
    service_host = os.getenv("KUBERNETES_SERVICE_HOST", "").strip()
    service_port = os.getenv("KUBERNETES_SERVICE_PORT", "").strip()
    service_account_dir = "/var/run/secrets/kubernetes.io/serviceaccount"
    service_account_ca = os.path.join(service_account_dir, "ca.crt")
    service_account_bearer_file = os.path.join(service_account_dir, "token")
    return bool(
        service_host
        and service_port
        and os.path.exists(service_account_bearer_file)
        and os.path.exists(service_account_ca)
    )


def default_settings() -> OidcSettingsDTO:
    issuer = os.getenv("OIDC_ISSUER", "").strip()

    ssl_verify = _env_bool("OIDC_SSL_VERIFY", False)

    return OidcSettingsDTO(
        oidc_enabled=_env_bool("OIDC_ENABLED", False),
        issuer=issuer,
        client_id=os.getenv("OIDC_CLIENT_ID", "").strip(),
        client_secret=os.getenv("OIDC_CLIENT_SECRET", "").strip(),
        client_secret_configured=bool(os.getenv("OIDC_CLIENT_SECRET", "").strip()),
        redirect_uri=os.getenv("OIDC_REDIRECT_URI", "").strip(),
        scopes=os.getenv("OIDC_SCOPE", "openid profile email").strip(),
        strict_discovery_document_validation=ssl_verify,
        ca_certificate="",
        api_base_url=os.getenv("APP_BASE_URL", "").strip(),
        config_source="env",
        kubernetes_enabled=kubernetes_enabled(),
    )


def get_or_create_settings_entity(session: Session) -> OidcConfigEntity:
    row = oidc_config.get(session)
    if row:
        return row

    defaults = default_settings()
    row = OidcConfigEntity(
        id=oidc_config.OIDC_CONFIG_ID,
        oidc_enabled=defaults.oidc_enabled,
        issuer=defaults.issuer,
        client_id=defaults.client_id,
        client_secret=defaults.client_secret,
        redirect_uri=defaults.redirect_uri,
        scopes=defaults.scopes,
        strict_discovery_document_validation=defaults.strict_discovery_document_validation,
        ca_certificate=defaults.ca_certificate,
        api_base_url=defaults.api_base_url,
    )
    return oidc_config.save(session, row)


def get_settings(session: Session) -> OidcSettingsDTO:
    if is_env_config_source():
        settings = default_settings()
    else:
        settings = oidc_entity_to_dto(get_or_create_settings_entity(session))

    # Redirect-relevant runtime settings can be overridden by env values.
    # Empty env values should not erase DB-managed OIDC settings.
    redirect_uri = os.getenv("OIDC_REDIRECT_URI", "").strip()
    api_base_url = os.getenv("APP_BASE_URL", "").strip()
    if redirect_uri:
        settings.redirect_uri = redirect_uri
    if api_base_url:
        settings.api_base_url = api_base_url
    settings.kubernetes_enabled = kubernetes_enabled()
    return settings


def update_settings(session: Session, request: UpdateOidcSettingsRequest) -> OidcSettingsDTO:
    if is_env_config_source():
        raise BadRequestError(
            "OIDC settings are managed via environment (OIDC_CONFIG_SOURCE=env) and are read-only",
        )

    row = get_or_create_settings_entity(session)
    row.oidc_enabled = request.oidc_enabled
    incoming_issuer = request.issuer
    incoming_client_id = request.client_id
    incoming_client_secret = request.client_secret
    incoming_redirect_uri = request.redirect_uri
    incoming_scopes = request.scopes

    # Never erase working OIDC config with empty payload fields.
    row.issuer = incoming_issuer or row.issuer
    row.client_id = incoming_client_id or row.client_id
    row.client_secret = incoming_client_secret or row.client_secret
    row.redirect_uri = incoming_redirect_uri or row.redirect_uri
    row.scopes = incoming_scopes or row.scopes
    row.strict_discovery_document_validation = request.strict_discovery_document_validation
    row.ca_certificate = request.ca_certificate if request.strict_discovery_document_validation else ""
    row.api_base_url = request.api_base_url
    row.updated_at = datetime.utcnow()
    return oidc_entity_to_dto(oidc_config.save(session, row))
