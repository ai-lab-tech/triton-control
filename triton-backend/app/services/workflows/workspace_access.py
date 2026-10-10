"""Owner-scoped Argo REST access for managed code-server workspaces."""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import Request
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from starlette.responses import Response, StreamingResponse

from app.core.identity import claims_from_user
from app.db.database import session_factory
from app.exceptions import BadGatewayError, BadRequestError, ForbiddenError, NotFoundError, UnauthorizedError
from app.repositories import developments, users
from app.services.kubernetes_client import api_client, in_cluster_namespace
from app.services.mlflow.tracking import WORKFLOW_OWNER_ANNOTATION, control_url
from app.services.workflows.config import get_config

TOKEN_KEY = "TRITON_CONTROL_ARGO_TOKEN"  # nosec B105 - environment variable name
OWNER_LABEL = "triton-control.ai/workflow-owner-user-id"


def workspace_url(namespace: str, name: str) -> str:
    return f"{control_url()}/api/workflows/workspaces/{namespace}/{name}/workflows"


def environment(namespace: str, name: str, secret_name: str) -> list[dict[str, Any]]:
    return [
        {"name": "TRITON_CONTROL_ARGO_URL", "value": workspace_url(namespace, name)},
        {"name": TOKEN_KEY, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": TOKEN_KEY}}},
    ]


def authenticate_workspace(namespace: str, name: str, token: str) -> dict[str, Any]:
    if not token or len(token) > 256:
        raise UnauthorizedError("Argo workspace authentication required")
    with session_factory() as session:
        workspace = developments.find_by_workload(session, namespace, name)
        if not workspace:
            raise UnauthorizedError("Argo workspace authentication invalid")
        secret_name, owner_id = workspace.secret_name, workspace.owner_user_id
    # Release the DB connection before waiting for Kubernetes.
    try:
        secret = client.CoreV1Api(api_client()).read_namespaced_secret(secret_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise UnauthorizedError("Argo workspace authentication invalid") from exc
        raise BadGatewayError("Argo workspace credential lookup failed") from exc
    try:
        expected = base64.b64decode((secret.data or {}).get(TOKEN_KEY, ""), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UnauthorizedError("Argo workspace authentication invalid") from exc
    if not expected or not secrets.compare_digest(token.encode(), expected):
        raise UnauthorizedError("Argo workspace authentication invalid")
    with session_factory() as session:
        owner = users.find_by_id(session, owner_id)
        if not owner or not owner.is_active or owner.role.lower() not in {"member", "admin"}:
            raise ForbiddenError("Argo workspace owner is not active or lacks access")
        return claims_from_user(owner, {"token_use": "workspace_argo"})  # nosec B105 - credential purpose


def workflow_path(workflow_name: str, action: str, claims: dict[str, Any]) -> str:
    """Only allow operations on workflows owned by this workspace's user."""
    if workflow_name and (
        len(workflow_name) > 253 or not re.fullmatch(r"[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?", workflow_name)
        or any(not part for part in workflow_name.split("."))
    ):
        raise BadRequestError("Invalid workflow name")
    config = get_config()
    namespace = config.namespace or in_cluster_namespace() or "triton-control"
    if action and action not in {"suspend", "resume", "terminate", "stop"}:
        raise ForbiddenError("This Argo operation is not available to workspace credentials")
    path = f"api/v1/workflows/{namespace}"
    if not workflow_name:
        return path
    try:
        workflow = client.CustomObjectsApi(api_client()).get_namespaced_custom_object(
            "argoproj.io", "v1alpha1", namespace, "workflows", workflow_name,
        )
    except ApiException as exc:
        if exc.status == 404:
            raise NotFoundError("Workflow not found") from exc
        raise BadGatewayError("Argo workflow ownership lookup failed") from exc
    owner_id = (workflow.get("metadata", {}).get("annotations") or {}).get(WORKFLOW_OWNER_ANNOTATION)
    if owner_id != str(claims["user_id"]):
        raise ForbiddenError("Workspace credentials can access only their owner's workflows")
    return f"{path}/{workflow_name}" + (f"/{action}" if action else "")


def owner_filtered_request(request: Request, claims: dict[str, Any]) -> Request:
    """Keep all supplied selectors, adding a mandatory owner condition."""
    query = list(request.query_params.multi_items())
    selectors = [value for key, value in query if key == "listOptions.labelSelector" and value]
    selectors.append(f"{OWNER_LABEL}={claims['user_id']}")
    query = [(key, value) for key, value in query if key != "listOptions.labelSelector"]
    query.append(("listOptions.labelSelector", ",".join(selectors)))
    scope = dict(request.scope, query_string=urlencode(query).encode())
    return Request(scope, receive=request.receive)


async def filter_list_response(response: Response, claims: dict[str, Any]) -> Response:
    """A selector narrows the query; the protected annotation verifies ownership.

    Older workflows may have user-supplied labels predating our managed label.
    Do not expose those workflows merely because their label names this owner.
    """
    if not 200 <= response.status_code < 300:
        return response
    if isinstance(response, StreamingResponse):
        try:
            chunks = [
                chunk.encode() if isinstance(chunk, str) else bytes(chunk)
                async for chunk in response.body_iterator
            ]
            body = b"".join(chunks)
        finally:
            if response.background is not None:
                await response.background()
    else:
        body = bytes(response.body)
    try:
        payload = json.loads(body)
        items = payload.get("items", [])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ValueError
        payload["items"] = [item for item in items if isinstance(item, dict) and (
            (item.get("metadata", {}).get("annotations") or {}).get(WORKFLOW_OWNER_ANNOTATION)
            == str(claims["user_id"])
        )]
    except (ValueError, AttributeError, TypeError) as exc:
        raise BadGatewayError("Invalid Argo workflow list response") from exc
    headers = {key: value for key, value in response.headers.items() if key not in {"content-length", "etag"}}
    return Response(json.dumps(payload).encode(), status_code=response.status_code, headers=headers,
                    media_type="application/json")


def upgrade_workspaces() -> int:
    from app.services.development import kubernetes as workspace_k8s

    with session_factory() as session:
        workloads = [(row.namespace, row.statefulset_name, row.secret_name) for row in developments.list_all(session)]
    for namespace, name, secret_name in workloads:
        workspace_k8s.enable_argo_access(namespace, name, secret_name)
    return len(workloads)
