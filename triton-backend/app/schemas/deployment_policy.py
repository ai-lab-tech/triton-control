"""Administrator-controlled deployment exposure policy."""

from typing import Literal

from pydantic import BaseModel


class DeploymentPolicyResponse(BaseModel):
    base_domain: str = ""
    default_base_domain: str = ""
    base_domain_source: Literal["environment", "admin"] = "environment"
    ingress_enabled: bool
    allow_http: bool
    default_allow_http: bool
    source: Literal["environment", "admin"]


class DeploymentPolicyUpdate(BaseModel):
    # null resets the override to the deployment configuration.
    allow_http: bool | None
    # Omitted preserves the override; null restores Helm/environment configuration.
    base_domain: str | None = None
