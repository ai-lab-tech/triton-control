"""Build exposure resources without Kubernetes or database I/O."""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any

import bcrypt

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
            manifests.append(
                {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "type": "Opaque",
                    "metadata": {"name": auth_secret, "namespace": namespace, "labels": {OWNER_LABEL: deployment_name}},
                    "stringData": {"users": f"{request.basic_auth_username}:{hashed}\n"},
                }
            )
        if not auth_secret:
            raise BadRequestError("Username and password are required when enabling basic authentication")
        manifests.append({
            "apiVersion": "traefik.io/v1alpha1",
            "kind": "Middleware",
            "metadata": {
                "name": auth_secret, "namespace": namespace,
                "labels": {OWNER_LABEL: deployment_name},
                "annotations": {"kubernetes.io/ingress.class": ingress_class()},
            },
            "spec": {"basicAuth": {
                "secret": auth_secret, "realm": "Triton authentication required", "removeHeader": True,
            }},
        })
        annotations[AUTH_SECRET_ANNOTATION] = auth_secret
        annotations[MIDDLEWARE_ANNOTATION] = f"{namespace}-{auth_secret}@kubernetescrd"
    spec: dict[str, Any] = {
        "ingressClassName": ingress_class(),
        "rules": [
            {
                "host": request.ingress_host,
                "http": {
                    "paths": [
                        {
                            "path": path,
                            "pathType": "Prefix",
                            "backend": {"service": {"name": service_name, "port": {"name": port}}},
                        }
                        for path, port in ([("/metrics", "metrics")] if allow_metrics else []) + [("/", "http")]
                    ]
                },
            }
        ],
    }
    if (request.ingress_scheme or "https") == "https":
        tls: dict[str, Any] = {"hosts": [request.ingress_host]}
        secret = os.getenv("TRITON_DEPLOY_INGRESS_TLS_SECRET", "").strip()
        issuer = os.getenv("TRITON_DEPLOY_CERT_MANAGER_CLUSTER_ISSUER", "").strip()
        if issuer:
            tls["secretName"] = f"{deployment_name}-ingress-tls"
            annotations["cert-manager.io/cluster-issuer"] = issuer
        elif secret:
            tls["secretName"] = secret
        spec["tls"] = [tls]
        annotations[f"{AUTH_PREFIX}router.tls"] = "true"
        annotations[f"{AUTH_PREFIX}router.entrypoints"] = (
            os.getenv("TRITON_DEPLOY_INGRESS_HTTPS_ENTRYPOINT", "websecure").strip() or "websecure"
        )
    else:
        annotations[f"{AUTH_PREFIX}router.entrypoints"] = (
            os.getenv("TRITON_DEPLOY_INGRESS_HTTP_ENTRYPOINT", "web").strip() or "web"
        )
    manifests.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": f"{deployment_name}-ingress",
                "namespace": namespace,
                "labels": {OWNER_LABEL: deployment_name},
                "annotations": annotations,
            },
            "spec": spec,
        }
    )
    return manifests
