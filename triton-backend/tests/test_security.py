"""Unit tests for the authentication and identity resolution layer.

Covers:
  ``extract_claims``           — bearer → local JWT, bearer → Keycloak OIDC,
                                 session cookie fallback, missing credentials.
  ``get_claims`` /
  ``get_claims_allow_pending`` — FastAPI dependency behaviour and pending-
                                 access policy enforcement.
  ``resolve_user``             — Local and OIDC user resolution, auto-creation
                                 of OIDC users on first login.
  ``claims_from_user``         — Claims dict enrichment from a ``UserEntity``.
"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.db.entities import UserEntity
from app.core.identity import (
    claims_from_user as _claims_from_user,
    normalize_provider as _normalize_provider,
    resolve_user,
    should_bootstrap_oidc_admin as _should_bootstrap_oidc_admin,
    is_pending_role,
)
from app.core.token_extractor import extract_claims as _extract_claims
from app.core.security import (
    _get_claims_with_access_policy,
    get_claims,
    get_claims_allow_pending,
)


class _ExecResult:
    def __init__(self, first=None, all_rows=None):
        self._first = first
        self._all = list(all_rows or [])

    def first(self):
        return self._first

    def all(self):
        return self._all


class _FakeSession:
    def __init__(self, exec_results):
        self._exec_results = list(exec_results)
        self.added = []
        self.commit_count = 0
        self.refresh_count = 0

    def exec(self, _query):
        return self._exec_results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commit_count += 1

    def refresh(self, _obj):
        self.refresh_count += 1


class SecurityHelpersTests(unittest.TestCase):
    def test_SecurityHelpers_RoleAndProviderInputs_ReturnNormalizedValues(self):
        # Arrange / Act
        oidc_provider = _normalize_provider("OIDC")
        unknown_provider = _normalize_provider("unknown")
        pending = is_pending_role(" pending ")
        admin_pending = is_pending_role("admin")

        # Assert
        self.assertEqual(oidc_provider, "oidc")
        self.assertEqual(unknown_provider, "local")
        self.assertTrue(pending)
        self.assertFalse(admin_pending)

    def test_ShouldBootstrapOidcAdmin_DifferentEnvironmentConditions_ReturnsExpectedDecision(self):
        # Arrange / Act / Assert
        with patch("app.core.identity._is_oidc_env_config_source", return_value=False):
            self.assertFalse(_should_bootstrap_oidc_admin(SimpleNamespace(), "a@example.com"))

        with patch("app.core.identity._is_oidc_env_config_source", return_value=True), patch(
            "app.core.identity.oidc_admin_allowlist", return_value=set()):
            self.assertFalse(_should_bootstrap_oidc_admin(SimpleNamespace(), "a@example.com"))

        with patch("app.core.identity._is_oidc_env_config_source", return_value=True), patch(
            "app.core.identity.oidc_admin_allowlist", return_value={"admin@example.com"}
        ), patch("app.core.identity.has_any_admin", return_value=False):
            self.assertTrue(_should_bootstrap_oidc_admin(SimpleNamespace(), "admin@example.com"))

    def test_ClaimsFromUser_ActiveOrPendingUser_ContainsCorrectAccessFlag(self):
        # Arrange
        user = UserEntity(
            id=3,
            email="u@example.com",
            name="U",
            role="viewer",
            auth_provider="oidc",
            oidc_subject="sub-1",
            assigned_instances=[],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )

        # Act
        claims = _claims_from_user(user, {"preferred_username": "u"})

        # Assert
        self.assertEqual(claims["sub"], "sub-1")
        self.assertEqual(claims["user_id"], 3)
        self.assertTrue(claims["access_allowed"])

        # Act
        user.role = "pending"
        claims = _claims_from_user(user, {})

        # Assert
        self.assertFalse(claims["access_allowed"])

    def test_ResolveUser_ExistingEmailAndNewSubject_AttachesOidcSubject(self):
        # Arrange
        existing = UserEntity(
            id=1,
            email="user@example.com",
            name="User",
            role="viewer",
            auth_provider="local",
            oidc_subject=None,
            assigned_instances=[],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        session = _FakeSession([_ExecResult(first=None), _ExecResult(first=existing)])
        claims = {"auth_provider": "oidc", "sub": "oidc-sub", "email": "user@example.com"}

        # Act
        user = resolve_user(session, claims, auto_create_oidc=True)

        # Assert
        self.assertIs(user, existing)
        self.assertEqual(existing.oidc_subject, "oidc-sub")
        self.assertEqual(session.commit_count, 1)
        self.assertEqual(session.refresh_count, 1)

    def test_ResolveUser_OidcAutoCreateAllowed_CreatesBootstrapAdminUser(self):
        # Arrange
        session = _FakeSession([_ExecResult(first=None), _ExecResult(first=None)])
        claims = {"auth_provider": "oidc", "sub": "oidc-sub", "email": "new@example.com", "name": "New"}

        # Act
        with patch("app.core.identity.should_bootstrap_oidc_admin", return_value=True):
            created = resolve_user(session, claims, auto_create_oidc=True)

        # Assert
        self.assertIsNotNone(created)
        self.assertEqual(created.role, "admin")
        self.assertTrue(created.is_active)
        self.assertEqual(session.commit_count, 1)

    def test_ResolveUser_AutoCreateDisabledWithoutMatch_ReturnsNone(self):
        # Arrange
        session = _FakeSession([_ExecResult(first=None), _ExecResult(first=None)])
        claims = {"auth_provider": "oidc", "sub": "oidc-sub", "email": "new@example.com"}

        # Act
        resolved = resolve_user(session, claims, auto_create_oidc=False)

        # Assert
        self.assertIsNone(resolved)


class SecurityClaimsFlowTests(unittest.IsolatedAsyncioTestCase):
    def _request(self, session_user=None):
        scope = {"type": "http", "headers": [], "session": {}}
        if session_user is not None:
            scope["session"]["user"] = session_user
        return Request(scope)

    async def test_ExtractClaims_LocalFailureAndOidcFallback_ReturnsOidcClaims(self):
        # Arrange
        request = self._request()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="x")

        # Act
        with patch("app.core.token_extractor.verify_access_token", return_value={"sub": "local"}):
            local_claims = await _extract_claims(request, creds)

        # Assert
        self.assertEqual(local_claims["sub"], "local")

        # Act
        with patch("app.core.token_extractor.verify_access_token", side_effect=ValueError("bad")), patch.object(
            __import__("app.core.token_extractor", fromlist=["auth"]).auth, "verify_token", AsyncMock(return_value={"sub": "oidc"})
        ):
            oidc_claims = await _extract_claims(request, creds)

        # Assert
        self.assertEqual(oidc_claims["sub"], "oidc")
        self.assertEqual(oidc_claims["auth_provider"], "oidc")

    async def test_ExtractClaims_SessionPresentOrMissing_ReturnsClaimsOrRaises(self):
        # Arrange
        request = self._request(session_user={"sub": "s1"})

        # Act
        claims = await _extract_claims(request, None)

        # Assert
        self.assertEqual(claims["sub"], "s1")

        # Act / Assert
        with self.assertRaises(HTTPException):
            await _extract_claims(self._request(), None)

    async def test_ExtractClaims_BearerInvalidButSessionPresent_ReturnsSessionClaims(self):
        # Arrange
        request = self._request(session_user={"sub": "session-user"})
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="stale")

        # Act
        with patch("app.core.token_extractor.verify_access_token", side_effect=ValueError("bad")), patch.object(
            __import__("app.core.token_extractor", fromlist=["auth"]).auth,
            "verify_token",
            AsyncMock(side_effect=ValueError("oidc down")),
        ):
            claims = await _extract_claims(request, creds)

        # Assert
        self.assertEqual(claims["sub"], "session-user")

    async def test_GetClaimsWithAccessPolicy_UserResolvedWithPendingControl_AllowsOrRejects(self):
        # Arrange
        request = self._request()
        fake_user = UserEntity(
            id=5,
            email="u@example.com",
            name="U",
            role="viewer",
            auth_provider="oidc",
            assigned_instances=[],
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )

        class _SessionCtx:
            def __enter__(self_inner):
                return SimpleNamespace()

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        # Act
        with patch("app.core.security.extract_claims", AsyncMock(return_value={"sub": "s", "email": "u@example.com"})), patch(
            "app.core.security.session_factory", return_value=_SessionCtx()
        ), patch("app.core.identity.resolve_user", return_value=fake_user):
            claims = await _get_claims_with_access_policy(request, None, allow_pending=False)

        # Assert
        self.assertEqual(claims["email"], "u@example.com")
        self.assertTrue(claims["access_allowed"])

        # Act / Assert
        fake_user.role = "pending"
        with patch("app.core.security.extract_claims", AsyncMock(return_value={"sub": "s", "email": "u@example.com"})), patch(
            "app.core.security.session_factory", return_value=_SessionCtx()
        ), patch("app.core.identity.resolve_user", return_value=fake_user):
            with self.assertRaises(HTTPException):
                await _get_claims_with_access_policy(request, None, allow_pending=False)

    async def test_GetClaimsWrappers_AllowPendingFlagVaries_DelegatesWithCorrectFlag(self):
        # Arrange
        request = self._request()

        # Act
        with patch("app.core.security._get_claims_with_access_policy", AsyncMock(return_value={"ok": True})) as policy:
            result = await get_claims(request, None)

        # Assert
            self.assertEqual(result, {"ok": True})
            self.assertFalse(policy.call_args.kwargs["allow_pending"])

        # Act
        with patch("app.core.security._get_claims_with_access_policy", AsyncMock(return_value={"ok": True})) as policy:
            result = await get_claims_allow_pending(request, None)

        # Assert
            self.assertEqual(result, {"ok": True})
            self.assertTrue(policy.call_args.kwargs["allow_pending"])


if __name__ == "__main__":
    unittest.main()
