"""Unit tests for the OIDC Backend-for-Frontend use cases.

Covers:
  ``login``          — OIDC disabled, config incomplete, canonical redirect.
  ``auth_callback``  — OAuth errors, successful login, session writing.
  ``logout``         — Session clearing and redirect.
  ``whoami``         — With and without an active session user.

All OIDC provider calls and DB sessions are replaced with mocks.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from authlib.integrations.base_client import OAuthError

from app.db.entities import UserEntity
from app.services.oidc.provider import (
    canonical_login_url_for_redirect_host,
    effective_redirect_uri,
    get_frontend_redirect_url,
    get_oauth,
)
from app.services.oidc.tls import get_oidc_tls_verify
from app.api.oidc_api import (
    auth_callback,
    login,
    logout,
    whoami,
)


class _Req:
    def __init__(self, url: str, query_params=None, session=None):
        self.url = url
        self.query_params = query_params or {}
        self.session = session or {}

    def url_for(self, name: str):
        if name == "auth_callback":
            return "http://api/auth/callback"
        return "http://api/unknown"


class OidcBffHelperTests(unittest.TestCase):
    def test_GetOauth_IssuerProvided_RegistersDiscoveryEndpoint(self):
        # Arrange / Act
        server_metadata = {
            "authorization_endpoint": "https://issuer/auth",
            "token_endpoint": "https://issuer/token",
            "userinfo_endpoint": "https://issuer/userinfo",
        }
        with patch("app.services.oidc.provider.OAuth") as oauth_cls:
            oauth = oauth_cls.return_value
            get_oauth("https://issuer/", "cid", "sec", "openid", server_metadata, True)

        # Assert
        oauth.register.assert_called_once()
        kwargs = oauth.register.call_args.kwargs
        self.assertEqual(kwargs["authorize_url"], "https://issuer/auth")
        self.assertEqual(kwargs["access_token_url"], "https://issuer/token")

    def test_GetOidcTlsVerify_CustomCertificate_UsesDefaultContextWithExtraCa(self):
        # Arrange
        certificate = "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----"

        # Act
        with patch("app.services.oidc.tls.create_default_context_with_extra_ca", return_value="context") as build_context:
            verify = get_oidc_tls_verify(True, certificate, False)

        # Assert
        self.assertEqual(verify, "context")
        build_context.assert_called_once_with(certificate, "OIDC")

    def test_FrontendRedirectUrl_ConfigPresentOrMissing_ReturnsExpectedUrl(self):
        # Arrange
        req = _Req("http://backend:8000/login")

        # Act
        with patch.dict(os.environ, {}, clear=True):
            default_redirected = get_frontend_redirect_url(req)

        with patch.dict(os.environ, {"FRONTEND_REDIRECT_URL": "http://frontend:4300/app"}, clear=True):
            redirected = get_frontend_redirect_url(req)

        # Assert
        self.assertEqual(default_redirected, "http://backend:4200/")
        self.assertEqual(redirected, "http://backend:4300/app")

    def test_RedirectHelpers_ConfigAndHostDifferences_ReturnCanonicalAndFallbackUrls(self):
        # Arrange
        req = _Req("http://backend:8000/login?x=1")

        # Act
        explicit_redirect = effective_redirect_uri(req, "http://custom/cb")
        fallback_redirect = effective_redirect_uri(req, "")
        canonical = canonical_login_url_for_redirect_host(req, "http://frontend:4200/callback")
        no_canonical = canonical_login_url_for_redirect_host(req, "")

        # Assert
        self.assertEqual(explicit_redirect, "http://custom/cb")
        self.assertEqual(fallback_redirect, "http://api/auth/callback")
        self.assertIn("canonical=1", canonical)
        self.assertIsNone(no_canonical)


class OidcBffRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_Login_OidcConfigurationsVary_ReturnsExpectedResponses(self):
        # Arrange
        req = _Req("http://backend:8000/login", query_params={}, session={})
        session = SimpleNamespace()

        disabled = SimpleNamespace(oidc_enabled=False, issuer="", client_id="", redirect_uri="", client_secret="", scopes="")

        # Act
        with patch("app.services.oidc.bff.get_settings", return_value=disabled):
            res = await login(req, session)

        # Assert
        self.assertEqual(res.status_code, 404)

        incomplete = SimpleNamespace(oidc_enabled=True, issuer="", client_id="", redirect_uri="", client_secret="x", scopes="openid")
        with patch("app.services.oidc.bff.get_settings", return_value=incomplete):
            res = await login(req, session)
        self.assertEqual(res.status_code, 400)

        missing_secret = SimpleNamespace(oidc_enabled=True, issuer="https://iss", client_id="cid", redirect_uri="http://cb", client_secret=" ", scopes="openid")
        with patch("app.services.oidc.bff.get_settings", return_value=missing_secret):
            res = await login(req, session)
        self.assertEqual(res.status_code, 500)

        settings = SimpleNamespace(
            oidc_enabled=True,
            issuer="https://iss",
            client_id="cid",
            redirect_uri="http://frontend/callback",
            client_secret="sec",
            scopes="openid",
            strict_discovery_document_validation=False,
            ca_certificate="",
            config_source="db",
        )
        with patch("app.services.oidc.bff.get_settings", return_value=settings), patch(
            "app.services.oidc.bff.canonical_login_url_for_redirect_host",
            return_value="http://frontend/login?canonical=1",
        ):
            res = await login(req, session)
        self.assertEqual(res.status_code, 307)

        oauth = SimpleNamespace(keycloak=SimpleNamespace(authorize_redirect=AsyncMock(return_value={"ok": True})))
        with patch("app.services.oidc.bff.get_settings", return_value=settings), patch(
            "app.services.oidc.bff.canonical_login_url_for_redirect_host", return_value=None
        ), patch("app.services.oidc.bff.load_server_metadata", new_callable=AsyncMock, return_value={}), patch(
            "app.services.oidc.bff.get_oidc_tls_verify", return_value=True
        ), patch("app.services.oidc.bff.get_oauth", return_value=oauth):
            res = await login(req, session)
        self.assertEqual(res, {"ok": True})

    async def test_AuthCallbackLogoutWhoami_SessionAndOauthOutcomes_ReturnExpectedPayloads(self):
        # Arrange
        req = _Req("http://backend:8000/auth/callback", session={})
        session = SimpleNamespace()

        disabled = SimpleNamespace(oidc_enabled=False, issuer="", client_id="", client_secret="", scopes="")

        # Act
        with patch("app.services.oidc.bff.get_settings", return_value=disabled):
            res = await auth_callback(req, session)

        # Assert
        self.assertEqual(res.status_code, 404)

        settings = SimpleNamespace(
            oidc_enabled=True,
            issuer="https://iss",
            client_id="cid",
            client_secret="sec",
            scopes="openid",
            strict_discovery_document_validation=False,
            ca_certificate="",
            config_source="db",
        )
        oauth = SimpleNamespace(
            keycloak=SimpleNamespace(
                authorize_access_token=AsyncMock(return_value={"at": "1"}),
                userinfo=AsyncMock(
                    return_value={
                        "sub": "s1",
                        "preferred_username": "u",
                        "email": "u@example.com",
                        "name": "U",
                    }
                ),
            )
        )
        with patch("app.services.oidc.bff.get_settings", return_value=settings), patch(
            "app.services.oidc.bff.load_server_metadata", new_callable=AsyncMock, return_value={}
        ), patch("app.services.oidc.bff.get_oidc_tls_verify", return_value=True), patch(
            "app.services.oidc.bff.get_oauth", return_value=oauth
        ), patch("app.services.oidc.bff.get_frontend_redirect_url", return_value="http://frontend/"):
            res = await auth_callback(req, session)

        # Assert
        self.assertEqual(res.status_code, 307)
        self.assertEqual(req.session["user"]["email"], "u@example.com")

        oauth_err = SimpleNamespace(
            keycloak=SimpleNamespace(authorize_access_token=AsyncMock(side_effect=OAuthError(error="bad")))
        )
        with patch("app.services.oidc.bff.get_settings", return_value=settings), patch(
            "app.services.oidc.bff.load_server_metadata", new_callable=AsyncMock, return_value={}
        ), patch("app.services.oidc.bff.get_oidc_tls_verify", return_value=True), patch(
            "app.services.oidc.bff.get_oauth", return_value=oauth_err
        ):
            res = await auth_callback(req, session)
        self.assertEqual(res.status_code, 400)

        # Act
        out = await logout(_Req("http://backend/logout", session={"user": {"x": 1}}))
        self.assertEqual(out, {"ok": True})

        no_session = await whoami(_Req("http://backend/api/whoami", session={}), session)
        self.assertEqual(no_session.status_code, 401)

        who_req = _Req("http://backend/api/whoami", session={"user": {"sub": "s1", "preferred_username": "u"}})
        user = UserEntity(
            id=2,
            email="u@example.com",
            name="U",
            role="viewer",
            auth_provider="oidc",
            oidc_subject="s1",
            assigned_instances=[],
            is_active=True,
        )
        with patch("app.services.oidc.bff.resolve_user", return_value=user):
            data = await whoami(who_req, session)
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["email"], "u@example.com")

        with patch("app.services.oidc.bff.resolve_user", return_value=None):
            data = await whoami(who_req, session)
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"]["sub"], "s1")


if __name__ == "__main__":
    unittest.main()
