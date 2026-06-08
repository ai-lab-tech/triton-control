"""Unit tests for the OIDC settings service and configuration layer.

Covers:
  - ``OIDC_CONFIG_SOURCE`` switching (env vs DB).
  - ``default_settings`` construction from environment variables.
  - ``get_or_create_settings_entity`` creation and retrieval.
  - ``update_settings`` persistence.
  - ``validate_oidc_connection`` success and failure paths.
  - Full ``get_settings`` / ``update_settings`` round-trips.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.db.entities import OidcConfigEntity
from app.exceptions import BadRequestError
from app.schemas import UpdateOidcSettingsRequest
from app.services.oidc.config import (
    _env_bool,
    default_settings,
    get_or_create_settings_entity,
    get_settings,
    is_env_config_source,
    oidc_config_source,
    update_settings,
)
from app.services.oidc.provider import validate_oidc_connection


class _FakeSession:
    def __init__(self, row=None):
        self.row = row
        self.added = []
        self.commit_count = 0
        self.refresh_count = 0

    def get(self, _model, _id):
        return self.row

    def add(self, obj):
        self.added.append(obj)
        self.row = obj

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        self.refresh_count += 1


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://example")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("bad", request=request, response=response)

    def json(self):
        return self._payload


class _FakeHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, _url, **_kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, _url, **_kwargs):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OidcSettingsTests(unittest.TestCase):
    def test_OidcConfigSource_EnvironmentValuesSet_ReturnsExpectedFlags(self):
        # Arrange / Act / Assert
        with patch.dict(os.environ, {"X": "true"}, clear=True):
            self.assertTrue(_env_bool("X", False))
        with patch.dict(os.environ, {"X": "0"}, clear=True):
            self.assertFalse(_env_bool("X", True))
        with patch.dict(os.environ, {"OIDC_CONFIG_SOURCE": "env"}, clear=True):
            self.assertEqual(oidc_config_source(), "env")
            self.assertTrue(is_env_config_source())
        with patch.dict(os.environ, {"OIDC_CONFIG_SOURCE": "db"}, clear=True):
            self.assertEqual(oidc_config_source(), "db")
            self.assertFalse(is_env_config_source())

    def test_DefaultSettings_EnvironmentConfigured_MapsValuesToDto(self):
        # Arrange / Act
        with patch.dict(
            os.environ,
            {
                "OIDC_ENABLED": "1",
                "OIDC_ISSUER": "https://issuer",
                "OIDC_CLIENT_ID": "cid",
                "OIDC_CLIENT_SECRET": "sec",
                "OIDC_REDIRECT_URI": "http://localhost/cb",
                "OIDC_SCOPE": "openid email",
                "OIDC_SSL_VERIFY": "false",
                "APP_BASE_URL": "http://api",
            },
            clear=True,
        ):
            dto = default_settings()

        # Assert
        self.assertTrue(dto.oidc_enabled)
        self.assertEqual(dto.issuer, "https://issuer")
        self.assertFalse(dto.strict_discovery_document_validation)

        # Arrange / Act
        with patch.dict(
            os.environ,
            {
                "OIDC_ENABLED": "1",
                "OIDC_ISSUER": "https://issuer",
                "OIDC_SSL_VERIFY": "true",
            },
            clear=True,
        ):
            dto = default_settings()

        # Assert
        self.assertTrue(dto.strict_discovery_document_validation)

    def test_GetOrCreateSettingsEntity_ExistingOrMissingRow_ReturnsPersistedEntity(self):
        # Arrange
        existing = OidcConfigEntity(id=1, oidc_enabled=True)
        session = _FakeSession(row=existing)

        # Act
        current = get_or_create_settings_entity(session)

        # Assert
        self.assertIs(current, existing)
        self.assertEqual(session.commit_count, 0)

        # Arrange
        session = _FakeSession(row=None)
        with patch("app.services.oidc.config.default_settings") as defaults:
            defaults.return_value = SimpleNamespace(
                oidc_enabled=True,
                issuer="iss",
                client_id="id",
                client_secret="sec",
                redirect_uri="uri",
                scopes="openid",
                strict_discovery_document_validation=False,
                ca_certificate="",
                api_base_url="api",
            )
            # Act
            created = get_or_create_settings_entity(session)

        # Assert
        self.assertEqual(created.id, 1)
        self.assertEqual(session.commit_count, 1)
        self.assertEqual(session.refresh_count, 1)

    def test_GetSettings_ConfigSourceEnvOrDb_ReturnsCorrespondingSettings(self):
        # Arrange / Act / Assert
        with patch("app.services.oidc.config.is_env_config_source", return_value=True), patch(
            "app.services.oidc.config.default_settings", return_value=SimpleNamespace(issuer="env")
        ):
            settings = get_settings(_FakeSession())
            self.assertEqual(settings.issuer, "env")

        with patch("app.services.oidc.config.is_env_config_source", return_value=False), patch(
            "app.services.oidc.config.get_or_create_settings_entity", return_value=SimpleNamespace()
        ), patch("app.services.oidc.config.oidc_entity_to_dto", return_value=SimpleNamespace(issuer="db")):
            settings = get_settings(_FakeSession())
            self.assertEqual(settings.issuer, "db")

    def _valid_request(self):
        return UpdateOidcSettingsRequest(
            oidc_enabled=True,
            issuer="http://issuer",
            client_id="cid",
            client_secret="sec",
            redirect_uri="http://localhost/callback",
            scopes="openid profile email",
            strict_discovery_document_validation=False,
            ca_certificate="",
            api_base_url="http://api",
        )

    def test_ValidateOidcConnection_MissingRequiredIssuer_RaisesHttpException(self):
        # Arrange
        req = self._valid_request()
        req.issuer = " "

        # Act / Assert
        with self.assertRaises(BadRequestError):
            validate_oidc_connection(req)

    def test_ValidateOidcConnection_BasicAuthRejectedAndPostFallback_CompletesWithoutError(self):
        # Arrange
        req = self._valid_request()
        responses = [
            _Resp(
                200,
                {
                    "jwks_uri": "https://issuer/jwks",
                    "authorization_endpoint": "https://issuer/auth",
                    "token_endpoint": "https://issuer/token",
                },
            ),
            _Resp(200, {"keys": [{"kid": "1"}]}),
            _Resp(302, {}),
            _Resp(401, {}),  # first token probe with basic auth
            _Resp(400, {"error": "invalid_grant"}),  # second probe with client_secret_post
        ]

        # Act / Assert
        with patch("app.services.oidc.provider.httpx.Client", return_value=_FakeHttpClient(responses)):
            validate_oidc_connection(req)

    def test_ValidateOidcConnection_InvalidClientError_RaisesHttpException(self):
        # Arrange
        req = self._valid_request()
        responses = [
            _Resp(
                200,
                {
                    "jwks_uri": "https://issuer/jwks",
                    "authorization_endpoint": "https://issuer/auth",
                    "token_endpoint": "https://issuer/token",
                },
            ),
            _Resp(200, {"keys": [{"kid": "1"}]}),
            _Resp(302, {}),
            _Resp(400, {"error": "invalid_client"}),
        ]

        # Act / Assert
        with patch("app.services.oidc.provider.httpx.Client", return_value=_FakeHttpClient(responses)):
            with self.assertRaisesRegex(BadRequestError, "invalid_client"):
                validate_oidc_connection(req)

    def test_ValidateOidcConnection_RequestErrorEncountered_RaisesMappedHttpException(self):
        # Arrange
        req = self._valid_request()
        request = httpx.Request("GET", "https://issuer/.well-known/openid-configuration")
        responses = [httpx.RequestError("offline", request=request)]

        # Act / Assert
        with patch("app.services.oidc.provider.httpx.Client", return_value=_FakeHttpClient(responses)):
            with self.assertRaisesRegex(BadRequestError, "OIDC connection test failed"):
                validate_oidc_connection(req)

    def test_UpdateSettings_DbWritableOrEnvManaged_UpdatesOrRejects(self):
        # Arrange
        row = OidcConfigEntity(
            id=1,
            oidc_enabled=False,
            issuer="old-iss",
            client_id="old-cid",
            client_secret="old-sec",
            redirect_uri="old-uri",
            scopes="old-scope",
            strict_discovery_document_validation=False,
            ca_certificate="old-cert",
            api_base_url="old-api",
        )
        session = _FakeSession(row=row)
        request = self._valid_request()

        # Act
        with patch("app.services.oidc.config.is_env_config_source", return_value=False), patch(
            "app.services.oidc.config.get_or_create_settings_entity", return_value=row
        ), patch("app.services.oidc.config.oidc_entity_to_dto", side_effect=lambda x: x):
            updated = update_settings(session, request)

        # Assert
        self.assertTrue(updated.oidc_enabled)
        self.assertEqual(updated.client_id, "cid")
        self.assertEqual(updated.ca_certificate, "")
        self.assertEqual(session.commit_count, 1)

        # Act / Assert
        with patch("app.services.oidc.config.is_env_config_source", return_value=True):
            with self.assertRaises(BadRequestError):
                update_settings(session, request)


if __name__ == "__main__":
    unittest.main()
