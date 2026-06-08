"""OIDC Backend-for-Frontend (BFF) use cases.

Implements the browser-facing OAuth2 authorisation code flow using
``authlib``'s Starlette integration and Starlette cookie sessions:
  ``login(request, session)``        — redirect the browser to the OIDC
                                        provider; raises appropriate errors
                                        when OIDC is disabled or misconfigured.
  ``auth_callback(request, session)``— exchange the authorisation code for
                                        tokens, validate the ID token, write
                                        user info into the session, and
                                        auto-create OIDC users on first login.
  ``logout(request)``                — clear the session and redirect.
  ``whoami(request, session)``       — return the session user or 401.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from authlib.integrations.starlette_client import OAuthError
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlmodel import Session

from app.core.identity import is_pending_role, resolve_user
from app.core.logging import get_logger
from app.services.oidc.config import get_settings
from app.services.oidc.provider import (
    canonical_login_url_for_redirect_host,
    effective_redirect_uri,
    get_frontend_redirect_url,
    get_oauth,
    load_server_metadata,
)
from app.services.oidc.tls import get_oidc_tls_verify

logger = get_logger(__name__)


def _with_default_return_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "returnUrl" not in query:
        query["returnUrl"] = "/dashboard"
    query_str = urlencode(query)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query_str, parsed.fragment))


async def login(request: Request, session: Session) -> object:
    settings = get_settings(session)
    if not settings.oidc_enabled:
        return JSONResponse({"error": "oidc_disabled", "detail": "OIDC login is disabled"}, status_code=404)
    if not settings.issuer or not settings.client_id or not settings.redirect_uri:
        return JSONResponse({"error": "config_error", "detail": "OIDC settings are incomplete"}, status_code=400)

    client_secret = settings.client_secret.strip()
    if not client_secret:
        return JSONResponse({"error": "config_error", "detail": "OIDC client_secret is missing"}, status_code=500)

    canonical = canonical_login_url_for_redirect_host(request, settings.redirect_uri)
    if canonical and request.query_params.get("canonical") != "1":
        return RedirectResponse(url=canonical)

    try:
        use_env_ca_bundle = settings.config_source == "env"
        server_metadata = await load_server_metadata(
            settings.issuer,
            settings.strict_discovery_document_validation,
            settings.ca_certificate,
            use_env_ca_bundle,
        )
        verify = get_oidc_tls_verify(
            settings.strict_discovery_document_validation,
            settings.ca_certificate,
            use_env_ca_bundle,
        )
        oauth = get_oauth(settings.issuer, settings.client_id, client_secret, settings.scopes, server_metadata, verify)
        redirect_uri = effective_redirect_uri(request, settings.redirect_uri)
        return await oauth.keycloak.authorize_redirect(request, redirect_uri=redirect_uri)
    except OAuthError as e:
        return JSONResponse({"error": "oauth_error", "detail": str(e)}, status_code=400)
    except httpx.HTTPError as e:
        logger.exception("OIDC login request failed")
        return JSONResponse(
            {"error": "oidc_unreachable", "detail": f"Could not reach OIDC provider: {e}"},
            status_code=502,
        )
    except RuntimeError as e:
        return JSONResponse({"error": "config_error", "detail": str(e)}, status_code=500)
    except Exception as e:
        logger.exception("Unexpected error during OIDC login")
        return JSONResponse({"error": "login_failed", "detail": str(e)}, status_code=500)


async def auth_callback(request: Request, session: Session) -> object:
    settings = get_settings(session)
    if not settings.oidc_enabled:
        return JSONResponse({"error": "oidc_disabled", "detail": "OIDC login is disabled"}, status_code=404)
    if not settings.issuer or not settings.client_id:
        return JSONResponse({"error": "config_error", "detail": "OIDC settings are incomplete"}, status_code=400)

    client_secret = settings.client_secret.strip()
    if not client_secret:
        return JSONResponse({"error": "config_error", "detail": "OIDC client_secret is missing"}, status_code=500)

    try:
        use_env_ca_bundle = settings.config_source == "env"
        server_metadata = await load_server_metadata(
            settings.issuer,
            settings.strict_discovery_document_validation,
            settings.ca_certificate,
            use_env_ca_bundle,
        )
        verify = get_oidc_tls_verify(
            settings.strict_discovery_document_validation,
            settings.ca_certificate,
            use_env_ca_bundle,
        )
        oauth = get_oauth(settings.issuer, settings.client_id, client_secret, settings.scopes, server_metadata, verify)
        token = await oauth.keycloak.authorize_access_token(request)
        userinfo = await oauth.keycloak.userinfo(token=token)

        request.session["user"] = {
            "sub": userinfo.get("sub"),
            "preferred_username": userinfo.get("preferred_username"),
            "email": userinfo.get("email"),
            "name": userinfo.get("name"),
            "auth_provider": "oidc",
        }

        return RedirectResponse(url=_with_default_return_url(get_frontend_redirect_url(request)))
    except OAuthError as e:
        return JSONResponse({"error": "oauth_error", "detail": str(e)}, status_code=400)
    except httpx.HTTPError as e:
        logger.exception("OIDC callback request failed")
        return JSONResponse(
            {"error": "oidc_unreachable", "detail": f"Could not reach OIDC provider: {e}"},
            status_code=502,
        )
    except RuntimeError as e:
        return JSONResponse({"error": "config_error", "detail": str(e)}, status_code=500)
    except Exception as e:
        logger.exception("Unexpected error during OIDC callback")
        return JSONResponse({"error": "callback_failed", "detail": str(e)}, status_code=500)


def logout(request: Request) -> dict[str, bool]:
    request.session.clear()
    return {"ok": True}


def whoami(request: Request, session: Session) -> object:
    """Return the current user's identity, enriched from the database."""
    session_user: Optional[Dict[str, Any]] = request.session.get("user")
    if not session_user:
        return JSONResponse({"authenticated": False}, status_code=401)

    user_entity = resolve_user(session, session_user, auto_create_oidc=True)
    if not user_entity:
        return {"authenticated": True, "user": session_user}

    enriched_user = {
        "sub": user_entity.oidc_subject or session_user.get("sub") or user_entity.email,
        "email": user_entity.email,
        "name": user_entity.name,
        "role": user_entity.role,
        "auth_provider": user_entity.auth_provider,
        "user_id": user_entity.id,
        "access_allowed": bool(user_entity.is_active and not is_pending_role(user_entity.role)),
        "preferred_username": session_user.get("preferred_username"),
    }
    return {"authenticated": True, "user": enriched_user}
