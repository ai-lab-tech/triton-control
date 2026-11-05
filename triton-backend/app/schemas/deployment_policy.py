"""Administrator-controlled deployment exposure policy."""

from typing import Literal

from pydantic import BaseModel


class DeploymentPolicyResponse(BaseModel):
    base_domain: str = ""
    ingress_enabled: bool
    allow_http: bool
    default_allow_http: bool
    source: Literal["environment", "admin"]


class DeploymentPolicyUpdate(BaseModel):
    # null resets the override to the deployment configuration.
    allow_http: bool | None
