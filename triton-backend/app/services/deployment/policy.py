"""Resolve and enforce administrator-controlled deployment exposure."""

import os
from typing import Any

from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import BadRequestError
from app.repositories import deployment_policy
from app.schemas.deployment_access import DeploymentAccessRequest
from app.schemas.deployment_policy import DeploymentPolicyResponse, DeploymentPolicyUpdate


def ingress_enabled() -> bool:
    return os.getenv("TRITON_DEPLOY_INGRESS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def validate_ingress(request: DeploymentAccessRequest) -> None:
    if request.ingress_host and not ingress_enabled():
        raise BadRequestError("Model Ingress is disabled by the administrator. Use cluster-only access.")


def get_policy(session: Session) -> DeploymentPolicyResponse:
    default = os.getenv("TRITON_DEPLOY_ALLOW_HTTP", "false").strip().lower() in {"1", "true", "yes", "on"}
    override = deployment_policy.get_override(session)
    return DeploymentPolicyResponse(
        ingress_enabled=ingress_enabled(),
        allow_http=default if override is None else override,
        default_allow_http=default,
        source="environment" if override is None else "admin",
    )


def update_policy(
    request: DeploymentPolicyUpdate, session: Session, claims: dict[str, Any],
) -> DeploymentPolicyResponse:
    require_admin(claims)
    deployment_policy.save_override(session, request.allow_http)
    return get_policy(session)


def validate_endpoint_policy(
    request: DeploymentAccessRequest, session: Session, *, existing_host: str | None = None,
    existing_scheme: str | None = None,
) -> None:
    validate_ingress(request)
    if not request.ingress_host or (request.ingress_scheme or "https") != "http":
        return
    # Existing HTTP endpoints are not silently disabled when policy changes.
    if existing_scheme == "http" and existing_host == request.ingress_host:
        return
    if not get_policy(session).allow_http:
        raise BadRequestError("HTTP endpoints are disabled by the administrator. Use HTTPS.")
