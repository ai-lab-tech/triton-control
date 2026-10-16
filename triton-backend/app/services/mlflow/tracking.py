"""Authenticate managed workloads and enforce immutable MLflow run creators."""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

from graphql import GraphQLError, OperationDefinitionNode, OperationType, parse
from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.client.rest import ApiException  # type: ignore[import-untyped]
from starlette.responses import Response

from app.db.database import session_factory
from app.exceptions import BadRequestError, ForbiddenError, ServiceUnavailableError, UnauthorizedError
from app.repositories import developments, users
from app.services.kubernetes_client import api_client

CREATOR_TAG = "mlflow.user"
TRACKING_TOKEN_KEY = "MLFLOW_TRACKING_TOKEN"  # nosec B105 - environment variable name
WORKFLOW_OWNER_ANNOTATION = "triton-control.ai/mlflow-owner-user-id"
_HEADER = "x-triton-mlflow-gateway"


def control_url() -> str:
    return os.getenv("DEVELOPMENT_PROFILE_API_URL", "http://triton-control:8000").rstrip("/")


def tracking_uri(kind: str, namespace: str, name: str) -> str:
    return f"{control_url()}/api/mlflow/tracking/{kind}/{namespace}/{name}"


def authenticate_workload(kind: str, namespace: str, name: str, token: str) -> dict[str, Any]:
    if not token or len(token) > 256:
        raise UnauthorizedError("MLflow workload authentication required")
    owner_id = None
    if kind == "workspaces":
        with session_factory() as session:
            workspace = developments.find_by_workload(session, namespace, name)
            if not workspace:
                raise UnauthorizedError("MLflow workload authentication invalid")
            secret_name = workspace.secret_name
            owner_id = workspace.owner_user_id
    elif kind == "workflows":
        secret_name = name
    else:
        raise UnauthorizedError("MLflow workload authentication invalid")
    # Do not retain a database connection while waiting for Kubernetes.
    try:
        secret = client.CoreV1Api(api_client()).read_namespaced_secret(secret_name, namespace)
    except ApiException as exc:
        if exc.status == 404:
            raise UnauthorizedError("MLflow workload authentication invalid") from exc
        raise
    expected = base64.b64decode((secret.data or {}).get(TRACKING_TOKEN_KEY, "")).decode()
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        raise UnauthorizedError("MLflow workload authentication invalid")
    if kind == "workflows":
        try:
            owner_id = int((secret.metadata.annotations or {})[WORKFLOW_OWNER_ANNOTATION])
        except (KeyError, ValueError, TypeError) as exc:
            raise UnauthorizedError("MLflow workload authentication invalid") from exc
    with session_factory() as session:
        if owner_id is None:
            raise UnauthorizedError("MLflow workload authentication invalid")
        owner = users.find_by_id(session, owner_id)
        if not owner or not owner.is_active or owner.role.lower() not in {"member", "admin"}:
            raise ForbiddenError("MLflow workload owner is not active or lacks access")
        return {"email": owner.email, "user_id": owner.id, "role": owner.role}


def enforce_creator(path: str, method: str, body: bytes, claims: dict[str, Any]) -> bytes:
    """Rewrite run creation; reject all subsequent edits to the creator tag.

    Both MLflow's REST and browser AJAX endpoints must obey the same rules.
    The legacy user_id field is overwritten too, since some UIs still display it.
    """
    path = path.strip("/")
    if path.startswith("ajax-api/"):
        path = path.removeprefix("ajax-")
    # Avoid ambiguous paths being interpreted differently by upstream routing.
    if any(part in {"", ".", ".."} for part in path.split("/")) and path:
        raise BadRequestError("Invalid MLflow API path")
    if "%" in path or "\\" in path:
        raise BadRequestError("Invalid MLflow API path")
    if method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return body
    if method.upper() == "POST" and path in {
        "api/3.0/mlflow/traces/search",
        "api/3.0/mlflow/traces/batchGetInfos",
        "api/3.0/mlflow/traces/calculate-filter-correlation",
        "api/3.0/mlflow/traces/metrics",
    }:
        # MLflow's trace UI uses POST for read-only queries in its v3 API.
        return body
    prefix = "api/2.0/mlflow/"
    if path in {"graphql", "api/2.0/mlflow/graphql"}:
        try:
            graph = json.loads(body)
            document = parse(graph["query"])
        except (ValueError, TypeError, KeyError, GraphQLError) as exc:
            raise BadRequestError("Invalid MLflow GraphQL query") from exc
        if any(
            isinstance(node, OperationDefinitionNode) and node.operation != OperationType.QUERY
            for node in document.definitions
        ):
            raise ForbiddenError("MLflow writes must use the tracking REST API")
        return body
    if path.startswith("api/2.0/mlflow-artifacts/"):
        return body
    if not path.startswith(prefix):
        # In particular, do not allow GraphQL mutations to bypass REST enforcement.
        raise ForbiddenError("MLflow writes must use the tracking REST API")
    action = path.removeprefix(prefix)
    creates = {"runs/create", "registered-models/create", "model-versions/create"}
    protected = action in creates or action in {
        "runs/set-tag", "runs/delete-tag", "runs/log-batch",
        "registered-models/set-tag", "registered-models/delete-tag",
        "model-versions/set-tag", "model-versions/delete-tag",
    }
    if action.endswith("/tags/mlflow.user"):
        raise ForbiddenError("MLflow run creator cannot be changed or deleted")
    logged_model = action == "logged-models" or action.endswith("/tags")
    if not protected and not logged_model:
        return body
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise BadRequestError("MLflow tracking request must contain a JSON object") from exc
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(tag, dict) for tag in tags):
        raise BadRequestError("MLflow tags must be a list of objects")
    creating = action in creates or (action == "logged-models" and method.upper() == "POST")
    if creating:
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise UnauthorizedError("MLflow creator identity is missing")
        payload["tags"] = [tag for tag in tags if tag.get("key") != CREATOR_TAG]
        payload["tags"].append({"key": CREATOR_TAG, "value": email})
        if action in {"runs/create", "model-versions/create"}:
            payload["user_id"] = email
        return json.dumps(payload).encode()
    if payload.get("key") == CREATOR_TAG or any(tag.get("key") == CREATOR_TAG for tag in tags):
        raise ForbiddenError("MLflow run creator cannot be changed or deleted")
    return body


def gateway_secret_name(deployment_name: str) -> str:
    return f"{deployment_name[:46].rstrip('-')}-tracking-gateway"


def expose_creator(path: str, response: Response) -> Response:
    """Populate UI creator fields from the persisted, protected model tag.

    MLflow 3.14 accepts model tags but leaves model-version user_id empty.
    Runs and logged models expose their protected mlflow.user tag.
    """
    path = path.strip("/").removeprefix("ajax-")
    if not path.startswith((
        "api/2.0/mlflow/model-versions", "api/2.0/mlflow/registered-models",
    )) or response.status_code >= 400:
        return response
    try:
        payload = json.loads(bytes(response.body))
    except (ValueError, TypeError):
        return response
    changed = False

    def visit(value: Any) -> None:
        nonlocal changed
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            tags = value.get("tags", [])
            creator = next((
                tag.get("value") for tag in tags if isinstance(tag, dict) and tag.get("key") == CREATOR_TAG
            ), None) if isinstance(tags, list) else None
            if creator:
                if "name" in value and "version" in value:
                    value["user_id"] = creator
                    changed = True
            for item in value.values():
                visit(item)

    visit(payload)
    if not changed:
        return response
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return Response(json.dumps(payload).encode(), status_code=response.status_code, headers=headers)


def gateway_header(server_url: str) -> dict[str, str]:
    from app.services.mlflow.proxy import _service_identity

    namespace, service_name = _service_identity(server_url)
    deployment_name = service_name.removesuffix("-service")
    try:
        secret = client.CoreV1Api(api_client()).read_namespaced_secret(gateway_secret_name(deployment_name), namespace)
    except ApiException as exc:
        if exc.status != 404:
            raise
        raise ServiceUnavailableError(
            "MLflow tracking gateway is not configured; an administrator must upgrade "
            "the installation using POST /api/mlflow/upgrade"
        ) from exc
    token = base64.b64decode((secret.data or {}).get("gateway-token", "")).decode()
    if not token:
        raise ServiceUnavailableError("MLflow tracking gateway is not configured; upgrade the installation")
    return {_HEADER: token}


def gateway_manifest(namespace: str, deployment_name: str, token: str) -> dict[str, Any]:
    # Bind MLflow itself to loopback. Only this authenticated gateway is reachable
    # from pods, including on clusters without NetworkPolicy enforcement.
    nginx = """pid /tmp/nginx.pid;
events { worker_connections 1024; }
http {
    access_log off;
    error_log /dev/stderr warn;
    client_body_temp_path /tmp/client-body;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    server {
        listen 5000;
        client_max_body_size 0;
        location / {
            if ($http_x_triton_mlflow_gateway != "TOKEN") { return 401; }
            proxy_pass http://127.0.0.1:5001;
            proxy_set_header X-Triton-Mlflow-Gateway "";
            proxy_set_header Host $http_host;
            proxy_read_timeout 120s;
            proxy_request_buffering off;
        }
    }
}
""".replace("TOKEN", token)
    return {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": gateway_secret_name(deployment_name), "namespace": namespace},
        "stringData": {"gateway-token": token, "nginx.conf": nginx},
    }
