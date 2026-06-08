"""OIDC service utilities: discovery, OAuth client construction, redirect-URL helpers,
and connection validation.

Pure functions — no HTTP handlers, no FastAPI router.
"""

from __future__ import annotations

import os
import secrets
import ssl
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import Request

from app.exceptions import AppError, BadRequestError
from app.schemas import UpdateOidcSettingsRequest
from app.services.oidc.tls import get_oidc_tls_verify


async def load_server_metadata(
    issuer: str,
    strict_discovery_document_validation: bool,
    ca_certificate: str = "",
    use_env_ca_bundle: bool = True,
) -> Dict[str, Any]:
    """Fetch and validate the OIDC discovery document for *issuer*."""
    discovery_url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    verify = get_oidc_tls_verify(
        strict_discovery_document_validation,
        ca_certificate,
        use_env_ca_bundle,
    )

    async with httpx.AsyncClient(timeout=10, verify=verify, follow_redirects=True) as client:
        response = await client.get(discovery_url)

    if response.status_code >= 400:
        raise RuntimeError(f"OIDC discovery endpoint returned HTTP {response.status_code}: {discovery_url}")

    try:
        metadata = response.json()
    except ValueError as exc:
        content_type = response.headers.get("content-type", "unknown")
        preview = response.text.strip().replace("\n", " ")[:160]
        raise RuntimeError(
            "OIDC discovery endpoint did not return JSON "
            f"(url={discovery_url}, content-type={content_type}, body={preview!r})"
        ) from exc

    if not isinstance(metadata, dict):
        raise RuntimeError(f"OIDC discovery endpoint returned invalid metadata: {discovery_url}")

    for required_field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not (metadata.get(required_field) or "").strip():
            raise RuntimeError(f"OIDC discovery document missing {required_field}: {discovery_url}")

    return metadata


def get_oauth(
    issuer: str,
    client_id: str,
    client_secret: str,
    scope: str,
    server_metadata: Dict[str, Any],
    verify: bool | str | ssl.SSLContext,
) -> OAuth:
    """Build an Authlib OAuth client pre-configured with *server_metadata*."""
    oauth = OAuth()
    oauth.register(
        name="keycloak",
        client_id=client_id,
        client_secret=client_secret,
        authorize_url=server_metadata["authorization_endpoint"],
        access_token_url=server_metadata["token_endpoint"],
        api_base_url=server_metadata.get("userinfo_endpoint"),
        client_kwargs={"scope": scope or "openid profile email", "verify": verify},
        **server_metadata,
    )
    return oauth


def get_frontend_redirect_url(request: Request) -> str:
    """Return the frontend base URL to redirect to after a successful OIDC login."""
    configured = (os.getenv("FRONTEND_REDIRECT_URL") or "").strip()
    if not configured:
        parsed_request = urlparse(str(request.url))
        return f"{parsed_request.scheme}://{parsed_request.hostname}:4200/"

    parsed_configured = urlparse(configured)
    parsed_request = urlparse(str(request.url))
    configured_host = (parsed_configured.hostname or "").lower()
    request_host = (parsed_request.hostname or "").lower()
    if configured_host and request_host and configured_host != request_host:
        scheme = parsed_configured.scheme or parsed_request.scheme
        port = f":{parsed_configured.port}" if parsed_configured.port else ":4200"
        path = parsed_configured.path or "/"
        return f"{scheme}://{parsed_request.hostname}{port}{path}"

    return configured


def effective_redirect_uri(request: Request, configured_redirect_uri: str) -> str:
    """Return the effective OAuth callback URI for this request."""
    configured = (configured_redirect_uri or "").strip()
    callback = str(request.url_for("auth_callback"))
    return configured or callback


def canonical_login_url_for_redirect_host(request: Request, configured_redirect_uri: str) -> Optional[str]:
    """Return a canonical login URL when the frontend lives on a different host, else None."""
    configured = (configured_redirect_uri or "").strip()
    if not configured:
        return None

    target = urlparse(configured)
    current = urlparse(str(request.url))
    if not target.netloc or target.netloc.lower() == current.netloc.lower():
        return None

    query = dict(parse_qsl(current.query, keep_blank_values=True))
    query["canonical"] = "1"
    query_string = urlencode(query)
    return urlunparse((target.scheme or current.scheme, target.netloc, current.path, "", query_string, ""))


def validate_oidc_connection(request: UpdateOidcSettingsRequest) -> None:
    """Probe the OIDC provider endpoints to verify that the supplied settings are reachable
    and the client credentials are accepted."""
    discovery_url = f"{request.issuer.rstrip('/')}/.well-known/openid-configuration"
    verify = get_oidc_tls_verify(
        request.strict_discovery_document_validation,
        request.ca_certificate,
        False,
    )

    try:
        with httpx.Client(timeout=10, verify=verify) as client:
            discovery_response = client.get(discovery_url)
            discovery_response.raise_for_status()
            discovery = discovery_response.json()

            jwks_uri = (discovery.get("jwks_uri") or "").strip()
            if not jwks_uri:
                raise BadRequestError("OIDC discovery document has no jwks_uri")

            for required_field in ("authorization_endpoint", "token_endpoint"):
                if not (discovery.get(required_field) or "").strip():
                    raise BadRequestError(
                        f"OIDC discovery document missing {required_field}",
                    )

            jwks_response = client.get(jwks_uri)
            jwks_response.raise_for_status()
            jwks = jwks_response.json()
            if not isinstance(jwks.get("keys"), list) or not jwks.get("keys"):
                raise BadRequestError("OIDC JWKS endpoint returned no keys")

            authorization_endpoint = discovery["authorization_endpoint"].strip()
            token_endpoint = discovery["token_endpoint"].strip()

            # Probe auth endpoint with configured client/redirect. Valid setups usually
            # return a redirect to login/consent; invalid client/redirect should fail.
            authorize_query = urlencode(
                {
                    "response_type": "code",
                    "client_id": request.client_id,
                    "redirect_uri": request.redirect_uri,
                    "scope": request.scopes or "openid profile email",
                    "state": secrets.token_urlsafe(16),
                    "nonce": secrets.token_urlsafe(16),
                    "prompt": "login",
                }
            )
            authorize_response = client.get(
                f"{authorization_endpoint}?{authorize_query}",
                follow_redirects=False,
            )
            if authorize_response.status_code >= 400:
                raise BadRequestError(
                    "OIDC authorize endpoint rejected client_id/redirect_uri "
                    f"(HTTP {authorize_response.status_code})",
                )

            # Probe token endpoint with authorization_code grant and a dummy code.
            # For valid client auth this should fail with invalid_grant/invalid_request,
            # while invalid credentials should fail with invalid_client / 401 / 403.
            token_probe = client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": "codex-oidc-validation-probe",
                    "redirect_uri": request.redirect_uri,
                },
                auth=(request.client_id, request.client_secret),
            )
            if token_probe.status_code in {401, 403}:
                # Fallback for providers expecting client_secret_post instead of HTTP Basic.
                token_probe = client.post(
                    token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": "codex-oidc-validation-probe",
                        "redirect_uri": request.redirect_uri,
                        "client_id": request.client_id,
                        "client_secret": request.client_secret,
                    },
                )
            if token_probe.status_code in {401, 403}:
                raise BadRequestError(
                    "OIDC token endpoint rejected client authentication "
                    "(tried client_secret_basic and client_secret_post)",
                )
            if token_probe.status_code >= 400:
                error_hint = ""
                try:
                    body = token_probe.json()
                    error_value = (body.get("error") or "").strip()
                    if error_value:
                        error_hint = error_value
                except ValueError:
                    error_hint = ""

                if error_hint == "invalid_client":
                    raise BadRequestError(
                        "OIDC token endpoint reported invalid_client (client_id/client_secret)",
                    )

    except AppError:
        raise
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        raise BadRequestError(
            f"OIDC connection test failed: {discovery_url} returned HTTP {status_code}",
        ) from exc
    except (httpx.RequestError, ValueError, RuntimeError, ssl.SSLError) as exc:
        raise BadRequestError(f"OIDC connection test failed: {exc}") from exc
