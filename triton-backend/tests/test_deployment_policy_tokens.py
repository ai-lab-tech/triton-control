"""Delegated deployment tokens may read but never change endpoint policy."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.security import _get_claims_with_access_policy


class PolicyTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_policy_read_allowed_for_all_deployment_token_types(self):
        for token in ("workspace_deployment", "workflow_deployment", "mlflow_workflow"):
            # Arrange
            request = SimpleNamespace(url=SimpleNamespace(path="/api/deployments/policy"), method="GET")
            with (
                patch("app.core.security.extract_claims", AsyncMock(return_value={"token_use": token})),
                patch("app.core.security._resolve_access_claims", return_value={"token_use": token}),
            ):
                # Act
                result = await _get_claims_with_access_policy(request, None)
            # Assert
            self.assertEqual(result["token_use"], token)

    async def test_policy_update_denied_for_all_deployment_token_types(self):
        for token in ("workspace_deployment", "workflow_deployment", "mlflow_workflow"):
            # Arrange
            request = SimpleNamespace(url=SimpleNamespace(path="/api/deployments/policy"), method="PUT")
            with patch("app.core.security.extract_claims", AsyncMock(return_value={"token_use": token})):
                # Act / Assert
                with self.assertRaises(HTTPException) as raised:
                    await _get_claims_with_access_policy(request, None)
            self.assertEqual(raised.exception.status_code, 403)
