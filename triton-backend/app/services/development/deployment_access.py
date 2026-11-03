"""Workspace-bound credentials for the MLflow deployment client."""

from __future__ import annotations

import base64
import binascii
import secrets
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from app.core.identity import claims_from_user
from app.db.database import session_factory
from app.exceptions import BadGatewayError, ForbiddenError, UnauthorizedError
from app.repositories import developments, users
from app.services.kubernetes_client import api_client

TOKEN_KEY = "TRITON_CONTROL_TOKEN"  # nosec B105 - environment variable name
TOKEN_PREFIX = "workspace-deployment:"


def create_token(namespace: str, workspace_name: str) -> str:
    return f"{TOKEN_PREFIX}{namespace}:{workspace_name}:{secrets.token_urlsafe(32)}"


def authenticate_token(token: str) -> dict[str, Any]:
    parts = token.split(":")
    if len(token) > 512 or len(parts) != 4 or not all(parts[1:]) or not token.startswith(TOKEN_PREFIX):
        raise UnauthorizedError("Workspace deployment authentication invalid")
    _, namespace, name, _ = parts
    with session_factory() as session:
        workspace = developments.find_by_workload(session, namespace, name)
        if not workspace:
            raise UnauthorizedError("Workspace deployment authentication invalid")
        secret_name, owner_id = workspace.secret_name, workspace.owner_user_id
    try:
        secret = client.CoreV1Api(api_client()).read_namespaced_secret(secret_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise UnauthorizedError("Workspace deployment authentication invalid") from exc
        raise BadGatewayError("Workspace deployment credential lookup failed") from exc
    try:
        expected = base64.b64decode((secret.data or {}).get(TOKEN_KEY, ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnauthorizedError("Workspace deployment authentication invalid") from exc
    if not expected or not secrets.compare_digest(token.encode(), expected):
        raise UnauthorizedError("Workspace deployment authentication invalid")
    with session_factory() as session:
        owner = users.find_by_id(session, owner_id)
        if not owner or not owner.is_active or owner.role.lower() not in {"member", "admin"}:
            raise ForbiddenError("Workspace owner is not active or lacks deployment access")
        return claims_from_user(owner, {
            "token_use": "workspace_deployment",  # nosec B105 - credential purpose
            "credential_version": owner.credential_version,
        })
