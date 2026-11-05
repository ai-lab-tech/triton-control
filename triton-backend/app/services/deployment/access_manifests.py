"""Populate exposure YAML templates without Kubernetes or database I/O."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, cast

import bcrypt
import yaml  # type: ignore[import-untyped]

from app.exceptions import BadRequestError
from app.schemas.deployment_access import DeploymentAccessRequest

AUTH_PREFIX = "traefik.ingress.kubernetes.io/"
# Kubernetes annotation key naming a Secret; this is not a credential.
AUTH_SECRET_ANNOTATION = "triton-control.io/auth-secret"  # nosec B105
MIDDLEWARE_ANNOTATION = f"{AUTH_PREFIX}router.middlewares"
OWNER_LABEL = "triton-control.io/deployment"


def ingress_class() -> str:
    return os.getenv("TRITON_DEPLOY_INGRESS_CLASS", "traefik").strip() or "traefik"


def validate_policy(request: DeploymentAccessRequest, *, creating: bool = False) -> None:
    if creating and request.basic_auth_enabled and not request.basic_auth_password:
        raise BadRequestError("Username and password are required when enabling basic authentication")
    if (request.ingress_host and (request.ingress_scheme or "https") == "https"
            and os.getenv("TRITON_DEPLOY_INGRESS_TLS_SECRET", "").strip()
            and os.getenv("TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER", "").strip()):
        raise BadRequestError("Configure either a TLS Secret or a cert-manager ClusterIssuer, not both")


def auth_secret_name(deployment_name: str) -> str:
    digest = hashlib.sha256(deployment_name.encode()).hexdigest()[:8]
    return f"{deployment_name[:32]}-{digest}-auth-{uuid.uuid4().hex[:8]}"


def _resource(template: str, name: str, namespace: str, deployment_name: str) -> dict[str, Any]:
    """Load a fresh resource so per-request changes never modify shared templates."""
    resource = cast(dict[str, Any], yaml.safe_load(Path(__file__).with_name(template).read_text(encoding="utf-8")))
    metadata = resource["metadata"]
    metadata["name"] = name
    metadata["namespace"] = namespace
    metadata["labels"][OWNER_LABEL] = deployment_name
    return resource


def access_manifests(
    request: DeploymentAccessRequest,
    namespace: str,
    deployment_name: str,
    service_name: str,
    *,
    auth_secret: str | None = None,
    allow_metrics: bool = True,
) -> list[dict[str, Any]]:
    validate_policy(request)
    if not request.ingress_host:
        return []
    manifests: list[dict[str, Any]] = []
    annotations: dict[str, str] = {}
    if request.basic_auth_enabled:
        if request.basic_auth_password and request.basic_auth_username:
            auth_secret = auth_secret_name(deployment_name)
            hashed = bcrypt.hashpw(request.basic_auth_password.get_secret_value().encode(), bcrypt.gensalt()).decode()
            secret_manifest = _resource("basic_auth_secret.yaml", auth_secret, namespace, deployment_name)
            secret_manifest["stringData"]["users"] = f"{request.basic_auth_username}:{hashed}\n"
            manifests.append(secret_manifest)
        if not auth_secret:
            raise BadRequestError("Username and password are required when enabling basic authentication")
        middleware = _resource("basic_auth_middleware.yaml", auth_secret, namespace, deployment_name)
        middleware["metadata"]["annotations"]["kubernetes.io/ingress.class"] = ingress_class()
        middleware["spec"]["basicAuth"]["secret"] = auth_secret
        manifests.append(middleware)
        annotations[AUTH_SECRET_ANNOTATION] = auth_secret
        annotations[MIDDLEWARE_ANNOTATION] = f"{namespace}-{auth_secret}@kubernetescrd"
    ingress = _resource("ingress.yaml", f"{deployment_name}-ingress", namespace, deployment_name)
    ingress["metadata"]["annotations"] = annotations
    spec = ingress["spec"]
    spec["ingressClassName"] = ingress_class()
    rule = spec["rules"][0]
    rule["host"] = request.ingress_host
    paths = rule["http"]["paths"]
    if not allow_metrics:
        paths = [path for path in paths if path["backend"]["service"]["port"]["name"] != "metrics"]
        rule["http"]["paths"] = paths
    for path in paths:
        path["backend"]["service"]["name"] = service_name
    if (request.ingress_scheme or "https") == "https":
        tls = spec["tls"][0]
        tls["hosts"] = [request.ingress_host]
        secret = os.getenv("TRITON_DEPLOY_INGRESS_TLS_SECRET", "").strip()
        issuer = os.getenv("TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER", "").strip()
        if issuer:
            tls["secretName"] = f"{deployment_name}-ingress-tls"
            annotations["cert-manager.io/cluster-issuer"] = issuer
        elif secret:
            tls["secretName"] = secret
        annotations[f"{AUTH_PREFIX}router.tls"] = "true"
        annotations[f"{AUTH_PREFIX}router.entrypoints"] = (
            os.getenv("TRITON_DEPLOY_INGRESS_HTTPS_ENTRYPOINT", "websecure").strip() or "websecure"
        )
    else:
        del spec["tls"]
        annotations[f"{AUTH_PREFIX}router.entrypoints"] = (
            os.getenv("TRITON_DEPLOY_INGRESS_HTTP_ENTRYPOINT", "web").strip() or "web"
        )
    manifests.append(ingress)
    return manifests
