"""Admin use cases for reading and updating persisted OIDC provider settings.

Provides:
  ``get_oidc_settings(session, claims)``              — admin-only read of the
                                                         current ``OidcSettingsDTO``.
  ``put_oidc_settings(request, http_request,
                      session, claims)``              — admin-only write;
    - Disabling OIDC requires at least one active local admin account.
    - Enabling OIDC (or changing provider details) triggers the preflight
      redirect flow so the new settings are verified before being persisted.
    - When the config source is environment-variable based, updates are
      rejected with ``BadRequestError``.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import BadRequestError
from app.schemas import OidcSettingsDTO, UpdateOidcSettingsRequest
from app.services.auth.local_auth import has_active_local_admin_login
from app.services.auth.oidc_preflight import build_oidc_preflight_context, start_preflight_redirect
from app.services.oidc.config import get_settings, is_env_config_source, update_settings
from app.services.oidc.provider import validate_oidc_connection


def get_oidc_settings(session: Session, claims: dict[str, Any]) -> OidcSettingsDTO:
    require_admin(claims)
    return _redact_client_secret(get_settings(session))


async def put_oidc_settings(
    request: UpdateOidcSettingsRequest,
    http_request: Request,
    session: Session,
    claims: dict[str, Any],
) -> object:
    require_admin(claims)
    if is_env_config_source():
        raise BadRequestError(
            "OIDC settings are managed via environment (OIDC_CONFIG_SOURCE=env) and cannot be changed via API",
        )
    current_settings = get_settings(session)
    request = _with_effective_client_secret(request, current_settings)

    if (
        not request.oidc_enabled
        and current_settings.oidc_enabled
        and not request.issuer
        and not request.client_id
        and not request.client_secret
        and not request.redirect_uri
        and not request.scopes
    ):
        request = UpdateOidcSettingsRequest(
            oidc_enabled=False,
            issuer=current_settings.issuer,
            client_id=current_settings.client_id,
            client_secret=current_settings.client_secret,
            redirect_uri=current_settings.redirect_uri,
            scopes=current_settings.scopes,
            strict_discovery_document_validation=request.strict_discovery_document_validation,
            ca_certificate=request.ca_certificate or current_settings.ca_certificate,
            api_base_url=request.api_base_url or current_settings.api_base_url,
        )

    if current_settings.oidc_enabled and not request.oidc_enabled:
        if not has_active_local_admin_login(session):
            raise BadRequestError(
                "Cannot disable OIDC: no active local admin user with password exists",
            )

    if request.oidc_enabled and not current_settings.oidc_enabled:
        validate_oidc_connection(request)
        context = build_oidc_preflight_context(request, claims)
        return await start_preflight_redirect(http_request, context)

    if request.oidc_enabled and current_settings.oidc_enabled:
        oidc_fields_changed = any(
            [
                request.issuer != current_settings.issuer,
                request.client_id != current_settings.client_id,
                request.client_secret != current_settings.client_secret,
                request.redirect_uri != current_settings.redirect_uri,
                request.scopes != current_settings.scopes,
                request.strict_discovery_document_validation
                != current_settings.strict_discovery_document_validation,
                request.ca_certificate != current_settings.ca_certificate,
            ]
        )
        if oidc_fields_changed:
            validate_oidc_connection(request)

    return _redact_client_secret(update_settings(session, request))


def _with_effective_client_secret(
    request: UpdateOidcSettingsRequest,
    current_settings: OidcSettingsDTO,
) -> UpdateOidcSettingsRequest:
    client_secret = request.client_secret or current_settings.client_secret
    if request.oidc_enabled and not client_secret:
        raise BadRequestError("OIDC enable requires client_secret")
    if client_secret == request.client_secret:
        return request
    return UpdateOidcSettingsRequest(
        oidc_enabled=request.oidc_enabled,
        issuer=request.issuer,
        client_id=request.client_id,
        client_secret=client_secret,
        redirect_uri=request.redirect_uri,
        scopes=request.scopes,
        strict_discovery_document_validation=request.strict_discovery_document_validation,
        ca_certificate=request.ca_certificate,
        api_base_url=request.api_base_url,
    )


def _redact_client_secret(settings: OidcSettingsDTO) -> OidcSettingsDTO:
    payload = settings.model_dump()
    payload["client_secret_configured"] = bool(settings.client_secret)
    payload["client_secret"] = ""  # nosec B105
    return OidcSettingsDTO(**payload)
