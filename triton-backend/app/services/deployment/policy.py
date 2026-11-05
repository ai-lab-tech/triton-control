"""Resolve and enforce administrator-controlled deployment exposure."""

import os
import re
from typing import Any, TypeVar

from sqlmodel import Session

from app.core.access_control import require_admin
from app.exceptions import BadRequestError
from app.repositories import deployment_policy
from app.schemas.deployment_access import DeploymentAccessRequest
from app.schemas.deployment_policy import DeploymentPolicyResponse, DeploymentPolicyUpdate

RequestT = TypeVar("RequestT", bound=DeploymentAccessRequest)


def base_domain() -> str:
    value = os.getenv("TRITON_DEPLOY_BASE_DOMAIN", "").strip().lower().rstrip(".")
    if value and (len(value) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")
    )):
        raise BadRequestError(
            "Administrator deployment base domain is invalid; configure a domain without wildcard or URL"
        )
    return value


def short_hostname(host: str | None) -> str | None:
    # Reading/removing existing endpoints must work even with a misconfigured domain.
    try:
        domain = base_domain()
    except BadRequestError:
        return host
    if host and domain and host.endswith("." + domain):
        label = host[:-(len(domain) + 1)]
        if "." not in label:
            return label
    return host


def resolve_hostname(request: RequestT, *, existing_host: str | None = None) -> RequestT:
    """Resolve once at the API/service boundary; queued tasks receive the full host."""
    host = request.ingress_host
    if not host or host == existing_host:
        return request
    domain = base_domain()
    if not domain:
        return request
    if "." in host:
        raise BadRequestError("Enter only the short hostname; the configured base domain is appended automatically")
    full_host = f"{host}.{domain}"
    if len(full_host) > 253:
        raise BadRequestError("Hostname plus base domain exceeds 253 characters")
    return request.model_copy(update={"ingress_host": full_host})


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
        base_domain=base_domain(),
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
