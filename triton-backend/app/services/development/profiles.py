"""Owner-scoped S3 profiles for a managed workspace, without browser authentication."""

from __future__ import annotations

import base64
import secrets

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from sqlmodel import Session

from app.core.identity import claims_from_user
from app.exceptions import ForbiddenError, UnauthorizedError
from app.repositories import developments, users
from app.schemas import S3ProfileDTO
from app.services.kubernetes_client import api_client
from app.services.storage.s3_profiles import list_profiles


def workspace_profiles(
    session: Session, namespace: str, statefulset_name: str, token: str,
) -> list[S3ProfileDTO]:
    if not token or len(token) > 256:
        raise UnauthorizedError("Workspace authentication required")
    workspace = developments.find_by_workload(session, namespace, statefulset_name)
    if not workspace:
        raise UnauthorizedError("Workspace authentication invalid")
    try:
        secret = client.CoreV1Api(api_client()).read_namespaced_secret(workspace.secret_name, workspace.namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise UnauthorizedError("Workspace authentication invalid") from exc
        raise
    expected = base64.b64decode((secret.data or {}).get("S3_PROFILE_TOKEN", "")).decode()
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise UnauthorizedError("Workspace authentication invalid")
    owner = users.find_by_id(session, workspace.owner_user_id)
    if not owner or not owner.is_active:
        raise ForbiddenError("Workspace owner is not active")
    # Resolve the current owner/role on every request. This token is accepted only
    # by this read-only endpoint; it cannot authenticate to other application APIs.
    return list_profiles(session, claims_from_user(owner, {}))
