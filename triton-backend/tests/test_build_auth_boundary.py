"""Workspace credentials must never become a general application login."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from app.core import security
from app.exceptions import UnauthorizedError


class BuildAuthenticationTests(unittest.TestCase):
    def request(self, path, handler):
        return Request({"type": "http", "method": "GET", "path": path,
                        "headers": [(b"x-triton-build-workspace", b"dev/code-one")],
                        "app": SimpleNamespace(state=SimpleNamespace(workspace_build_authenticator=handler))})

    def test_scoped_authentication_uses_workload_verifier_without_browser_session(self):
        # Arrange
        handler = MagicMock(return_value={"email": "engineer@test", "build_task_id": "task"})
        request = self.request("/api/builds/options", handler)
        token = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fixture")
        with patch.object(security, "session_factory"), patch.object(security, "extract_claims") as normal_auth:
            # Act
            claims = asyncio.run(security.get_claims(request, token))
            # Assert
            self.assertEqual(claims["build_task_id"], "task")
            self.assertEqual(handler.call_args.args[1:], ("dev/code-one", "fixture"))
            normal_auth.assert_not_called()

    def test_workspace_header_cannot_authenticate_to_general_operations_or_deployment(self):
        # Arrange
        handler = MagicMock()
        token = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fixture")
        for path in ("/api/operations/" + "a" * 32 + "/retry", "/api/deployments", "/api/users", "/api/builds-evil"):
            request = self.request(path, handler)
            # Act
            with self.subTest(path=path), self.assertRaises(HTTPException) as error:
                asyncio.run(security.get_claims(request, token))
            # Assert
            self.assertEqual(error.exception.status_code, 403)
        handler.assert_not_called()

    def test_revoked_credential_remains_unauthorized_without_session_fallback(self):
        # Arrange
        handler = MagicMock(side_effect=UnauthorizedError("Workspace authentication invalid"))
        request = self.request("/api/builds", handler)
        token = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expired")
        with patch.object(security, "session_factory"), patch.object(security, "extract_claims") as normal_auth:
            # Act
            with self.assertRaises(HTTPException) as error:
                asyncio.run(security.get_claims(request, token))
            # Assert
            self.assertEqual(error.exception.status_code, 401)
            normal_auth.assert_not_called()
