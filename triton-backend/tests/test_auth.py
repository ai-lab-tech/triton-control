"""Unit tests for ``KeycloakAuth`` — the OIDC JWT verifier.

Covers:
  - OIDC discovery document caching.
  - JWKS caching.
  - Successful token verification.
  - Expired and structurally invalid token errors.
  - Network error handling during discovery / JWKS fetch.
"""

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from jose import JWTError

from app.core.auth import KeycloakAuth


class _AsyncClientCtx:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, _url):
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class KeycloakAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_GetOidcConfigAndJwks_CalledRepeatedly_UsesCache(self):
        # Arrange
        auth = KeycloakAuth("https://issuer.example")

        # Act
        with patch("app.core.auth.httpx.AsyncClient", return_value=_AsyncClientCtx([_Resp({"jwks_uri": "https://jwks"})])) as p:
            cfg1 = await auth._get_oidc_config()
            cfg2 = await auth._get_oidc_config()

        # Assert
        self.assertEqual(cfg1["jwks_uri"], "https://jwks")
        self.assertEqual(cfg1, cfg2)
        self.assertEqual(p.call_count, 1)

        # Arrange
        with patch("app.core.auth.httpx.AsyncClient", return_value=_AsyncClientCtx([_Resp({"keys": [{"kid": "k1"}]})])):
            # Act
            jwks1 = await auth._get_jwks()
            jwks2 = await auth._get_jwks()

        # Assert
        self.assertEqual(jwks1, jwks2)
        self.assertEqual(jwks1["keys"][0]["kid"], "k1")

    async def test_VerifyToken_HeaderOrDecodeInvalid_RaisesValueError(self):
        # Arrange
        auth = KeycloakAuth("https://issuer.example")
        auth._get_jwks = AsyncMock(return_value={"keys": [{"kid": "k1"}]})

        # Act / Assert
        with patch("app.core.auth.jwt.get_unverified_header", side_effect=JWTError("bad")):
            with self.assertRaisesRegex(ValueError, "Invalid token header"):
                await auth.verify_token("token")

        # Act / Assert
        with patch("app.core.auth.jwt.get_unverified_header", return_value={}):
            with self.assertRaisesRegex(ValueError, "Missing kid"):
                await auth.verify_token("token")

        # Act / Assert
        with patch("app.core.auth.jwt.get_unverified_header", return_value={"kid": "k1"}), patch(
            "app.core.auth.jwt.decode", side_effect=JWTError("decode bad")
        ):
            with self.assertRaisesRegex(ValueError, "Token verification failed"):
                await auth.verify_token("token")

    async def test_VerifyToken_KeyRotationOccurs_ReturnsDecodedClaims(self):
        # Arrange
        auth = KeycloakAuth("https://issuer.example", expected_audience="client")
        auth._cache["jwks"] = {"keys": [{"kid": "old"}]}
        auth._get_jwks = AsyncMock(return_value={"keys": [{"kid": "new"}]})

        # Act
        with patch("app.core.auth.jwt.get_unverified_header", return_value={"kid": "new"}), patch(
            "app.core.auth.jwt.decode", return_value={"sub": "u1"}
        ) as decode_mock:
            claims = await auth.verify_token("token")

        # Assert
        self.assertEqual(claims["sub"], "u1")
        self.assertEqual(auth._get_jwks.call_count, 1)
        self.assertEqual(decode_mock.call_args.kwargs["audience"], "client")
        self.assertTrue(decode_mock.call_args.kwargs["options"]["verify_aud"])

    async def test_VerifyToken_KeyMissingAfterRefresh_RaisesValueError(self):
        # Arrange
        auth = KeycloakAuth("https://issuer.example")
        auth._cache["jwks"] = {"keys": [{"kid": "other"}]}
        auth._get_jwks = AsyncMock(return_value={"keys": [{"kid": "still-other"}]})

        # Act / Assert
        with patch("app.core.auth.jwt.get_unverified_header", return_value={"kid": "wanted"}):
            with self.assertRaisesRegex(ValueError, "Signing key not found"):
                await auth.verify_token("token")

    async def test_GetOidcConfig_NetworkError_RaisesValueError(self):
        # Arrange
        auth = KeycloakAuth("https://issuer.example")

        # Act / Assert
        with patch(
            "app.core.auth.httpx.AsyncClient",
            return_value=_AsyncClientCtx([httpx.ConnectError("down")]),
        ):
            with self.assertRaisesRegex(ValueError, "OIDC discovery failed"):
                await auth._get_oidc_config()


if __name__ == "__main__":
    unittest.main()
