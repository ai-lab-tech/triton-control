"""Read-only Kubernetes observations; no provider calls or inferred releases."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Condition(BaseModel):
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


class ContainerState(BaseModel):
    name: str
    kind: Literal["regular", "init"]
    image: str
    image_id: str | None = None
    ready: bool = False
    restarts: int = 0
    state: Literal["waiting", "running", "terminated", "unknown"] = "unknown"
    reason: str | None = None
    message: str | None = None
    exit_code: int | None = None
    last_termination_reason: str | None = None


class PodState(BaseModel):
    uid: str
    name: str
    node: str | None = None
    phase: str
    ready: bool
    created_at: datetime | None = None
    deletion_timestamp: datetime | None = None
    release: str | None = None
    revision: str | None = None
    reason: str | None = None
    message: str | None = None
    conditions: list[Condition] = Field(default_factory=list)
    containers: list[ContainerState] = Field(default_factory=list)


class DeploymentState(BaseModel):
    uid: str
    generation: int
    observed_generation: int | None = None
    awaiting_observation: bool
    desired: int
    updated: int
    ready: int
    available: int
    conditions: list[Condition] = Field(default_factory=list)


class InstancePodsResponse(BaseModel):
    kubernetes_available: bool
    health_live: bool
    health_ready: bool
    deployment: DeploymentState | None = None
    pods: list[PodState] = Field(default_factory=list)
