"""Use cases for the OIDC preflight verification flow.

The preflight flow lets an admin verify a new OIDC provider before committing
the settings: the admin is redirected to the OIDC login, comes back via a
callback, and only then are the new settings persisted.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuthError
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import AppError, BadRequestError, InternalError
from app.repositories import users
from app.schemas import UpdateOidcSettingsRequest
from app.services.oidc.config import is_env_config_source, update_settings
from app.services.oidc.provider import (
    get_frontend_redirect_url,
    get_oauth,
    load_server_metadata,
    validate_oidc_connection,
)
from app.services.oidc.tls import get_oidc_tls_verify

OIDC_PREFLIGHT_SESSION_KEY = "oidc_preflight_setup"


def build_oidc_preflight_context(
    request: UpdateOidcSettingsRequest,
    claims: dict[str, Any],
) -> dict[str, Any]:
    local_email = (claims.get("email") or "").strip().lower()
    local_name = (claims.get("name") or "").strip()
    if not local_email:
        raise BadRequestError("Current local login must provide email")

    settings_payload = {
        "oidc_enabled": True,
        "issuer": request.issuer,
        "client_id": request.client_id,
        "client_secret": request.client_secret,
        "redirect_uri": request.redirect_uri,
        "scopes": request.scopes,
        "strict_discovery_document_validation": request.strict_discovery_document_validation,
        "ca_certificate": request.ca_certificate,
        "api_base_url": request.api_base_url,
    }
    return {
        "expected_email": local_email,
        "local_name": local_name,
        "settings": settings_payload,
    }


async def start_preflight_redirect(http_request: Request, context: dict[str, Any]) -> dict[str, Any]:
    http_request.session[OIDC_PREFLIGHT_SESSION_KEY] = context
    settings_payload = context["settings"]
    strict = bool(settings_payload.get("strict_discovery_document_validation", False))
    ca_certificate = settings_payload.get("ca_certificate", "")
    server_metadata = await load_server_metadata(settings_payload["issuer"], strict, ca_certificate, False)
    verify = get_oidc_tls_verify(strict, ca_certificate, False)
    oauth = get_oauth(
        settings_payload["issuer"],
        settings_payload["client_id"],
        settings_payload["client_secret"],
        settings_payload["scopes"],
        server_metadata,
        verify,
    )
    callback_uri = str(http_request.url_for("oidc_preflight_callback"))
    redirect_response = await oauth.keycloak.authorize_redirect(http_request, redirect_uri=callback_uri)
    authorize_url = redirect_response.headers.get("location")
    if not authorize_url:
        raise InternalError("Could not build OIDC authorization URL")
    return {"preflight_required": True, "authorize_url": authorize_url}


async def start_oidc_preflight(
    request: UpdateOidcSettingsRequest,
    http_request: Request,
    claims: dict[str, Any],
) -> object:
    require_admin(claims)
    if is_env_config_source():
        raise BadRequestError(
            "OIDC settings are managed via environment (OIDC_CONFIG_SOURCE=env); "
            "preflight setup via API is disabled",
        )
    validate_oidc_connection(request)
    context = build_oidc_preflight_context(request, claims)
    return await start_preflight_redirect(http_request, context)


async def oidc_preflight_callback(http_request: Request, session: Session) -> object:
    context = http_request.session.get(OIDC_PREFLIGHT_SESSION_KEY)
    if not context:
        raise BadRequestError("OIDC preflight session is missing or expired")

    settings_payload = context.get("settings") or {}
    expected_email = (context.get("expected_email") or "").strip().lower()
    local_name = (context.get("local_name") or "").strip()
    frontend_url = get_frontend_redirect_url(http_request).rstrip("/")
    settings_redirect_base = f"{frontend_url}/settings"
    callback_result_base = f"{settings_redirect_base}?{urlencode({'oidc_preflight': 'failed'})}"

    try:
        strict = bool(settings_payload.get("strict_discovery_document_validation", False))
        ca_certificate = settings_payload.get("ca_certificate", "")
        server_metadata = await load_server_metadata(
            settings_payload.get("issuer", ""),
            strict,
            ca_certificate,
            False,
        )
        verify = get_oidc_tls_verify(strict, ca_certificate, False)
        oauth = get_oauth(
            settings_payload.get("issuer", ""),
            settings_payload.get("client_id", ""),
            settings_payload.get("client_secret", ""),
            settings_payload.get("scopes", ""),
            server_metadata,
            verify,
        )
        token = await oauth.keycloak.authorize_access_token(http_request)
        userinfo = await oauth.keycloak.userinfo(token=token)

        oidc_email = (userinfo.get("email") or "").strip().lower()
        oidc_sub = (userinfo.get("sub") or "").strip()
        oidc_name = (userinfo.get("name") or "").strip()

        if not oidc_email or not oidc_sub:
            raise BadRequestError("OIDC login did not return email and subject")
        if oidc_email != expected_email:
            raise BadRequestError("OIDC user email does not match current local admin email")

        current_user = users.find_by_oidc_subject(session, oidc_sub)
        if not current_user:
            current_user = users.find_by_email(session, expected_email)
        should_save_user = True
        if not current_user:
            current_user = users.create(
                session,
                email=expected_email,
                name=oidc_name or local_name or expected_email.split("@", 1)[0],
                role="admin",
                auth_provider="oidc",
                oidc_subject=oidc_sub,
                assigned_instances=[],
                is_active=True,
            )
            should_save_user = False
        else:
            current_user.email = expected_email
            current_user.name = oidc_name or local_name or current_user.name
            current_user.role = "admin"
            current_user.auth_provider = "oidc"
            current_user.oidc_subject = oidc_sub
            current_user.is_active = True

        if should_save_user:
            users.save(session, current_user, refresh=False)
        update_settings(session, UpdateOidcSettingsRequest(**settings_payload))

        http_request.session["user"] = {
            "sub": oidc_sub,
            "preferred_username": userinfo.get("preferred_username"),
            "email": oidc_email,
            "name": oidc_name or local_name,
            "auth_provider": "oidc",
        }
        http_request.session.pop(OIDC_PREFLIGHT_SESSION_KEY, None)
        return RedirectResponse(url=f"{settings_redirect_base}?{urlencode({'oidc_preflight': 'ok'})}")

    except AppError as exc:
        session.rollback()
        http_request.session.pop(OIDC_PREFLIGHT_SESSION_KEY, None)
        return RedirectResponse(
            url=f"{callback_result_base}&{urlencode({'detail': exc.detail})}",
            status_code=302,
        )
    except OAuthError as exc:
        session.rollback()
        http_request.session.pop(OIDC_PREFLIGHT_SESSION_KEY, None)
        return RedirectResponse(
            url=f"{callback_result_base}&{urlencode({'detail': str(exc)})}",
            status_code=302,
        )
    except Exception as exc:
        session.rollback()
        http_request.session.pop(OIDC_PREFLIGHT_SESSION_KEY, None)
        return RedirectResponse(
            url=f"{callback_result_base}&{urlencode({'detail': f'Unexpected error: {exc}'})}",
            status_code=302,
        )
