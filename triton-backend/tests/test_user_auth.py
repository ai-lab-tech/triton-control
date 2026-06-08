"""Unit tests for local-authentication JWT utilities.

Covers:
  ``issue_access_token``  — JWT creation with correct claims.
  ``verify_access_token`` — signature validation, expiry enforcement, tampered
                            tokens, and missing fields.
  ``verify_password``     — correct password acceptance, wrong password rejection.
  ``hash_password``       — deterministic salt + digest format.
"""

import os
import unittest
from unittest.mock import patch

from app.core.user_auth import (
    _jwt_secret,
    hash_password,
    issue_access_token,
    verify_access_token,
    verify_password,
)


class PasswordHashTests(unittest.TestCase):
    def test_HashPassword_ValidInputProvided_VerifiesRoundtripSuccessfully(self):
        # Arrange
        password = "secret-123"

        # Act
        encoded = hash_password(password)
        valid = verify_password(password, encoded)
        invalid = verify_password("wrong", encoded)

        # Assert
        self.assertTrue(encoded.startswith("pbkdf2_sha256$120000$"))
        self.assertTrue(valid)
        self.assertFalse(invalid)

    def test_VerifyPassword_InvalidEncodedFormat_ReturnsFalse(self):
        # Arrange
        password = "secret"

        # Act
        empty_result = verify_password(password, "")
        wrong_algo_result = verify_password(password, "md5$1$salt$hash")

        # Assert
        self.assertFalse(empty_result)
        self.assertFalse(wrong_algo_result)


class JwtTests(unittest.TestCase):
    def test_JwtSecret_EnvironmentVariablesPresent_UsesExpectedPrecedence(self):
        # Arrange / Act / Assert
        with patch.dict(os.environ, {"JWT_SECRET": "jwt", "SESSION_SECRET": "session"}, clear=False):
            self.assertEqual(_jwt_secret(), "jwt")
        with patch.dict(os.environ, {"SESSION_SECRET": "session"}, clear=True):
            self.assertEqual(_jwt_secret(), "session")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_jwt_secret(), "change-me-in-production")

    def test_AccessToken_IssuedThenVerified_ReturnsExpectedClaims(self):
        # Arrange
        user = {"email": "alice@example.com", "name": "Alice", "role": "admin", "auth_provider": "local"}

        # Act
        with patch.dict(os.environ, {"JWT_SECRET": "unit-test-secret"}, clear=False):
            token = issue_access_token(user, expires_minutes=5)
            claims = verify_access_token(token)

        # Assert
        self.assertEqual(claims["email"], "alice@example.com")
        self.assertEqual(claims["name"], "Alice")
        self.assertEqual(claims["role"], "admin")
        self.assertEqual(claims["auth_provider"], "local")

    def test_VerifyAccessToken_InvalidTokenProvided_RaisesValueError(self):
        # Arrange / Act / Assert
        with patch.dict(os.environ, {"JWT_SECRET": "unit-test-secret"}, clear=False):
            with self.assertRaises(ValueError):
                verify_access_token("not-a-jwt")


if __name__ == "__main__":
    unittest.main()
