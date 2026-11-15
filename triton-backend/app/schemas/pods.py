"""Versioned, I/O-free workload and bounded log response contracts."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PodCondition(BaseModel):
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


class ContainerState(BaseModel):
    name: str
    kind: Literal["regular", "init", "sidecar"]
    ready: bool = False
    restart_count: int = 0
    state: str = "unknown"
    reason: str | None = None
    message: str | None = None
    exit_code: int | None = None
    last_termination_reason: str | None = None
    image: str | None = None
    image_id: str | None = None


class PodState(BaseModel):
    uid: str
    name: str
    node: str | None = None
    phase: str
    ready: bool
    restarts: int
    created_at: datetime | None = None
    terminating: bool
    release: str | None = None
    revision: str | None = None
    current_revision: bool = False
    reason: str | None = None
    message: str | None = None
    conditions: list[PodCondition] = Field(default_factory=list)
    containers: list[ContainerState] = Field(default_factory=list)


class DeploymentState(BaseModel):
    uid: str
    name: str
    generation: int
    observed_generation: int | None = None
    stale: bool
    desired: int
    updated: int
    ready: int
    available: int
    release: str | None = None
    revision: str | None = None
    conditions: list[PodCondition] = Field(default_factory=list)


class InstancePods(BaseModel):
    schema_version: Literal[1] = 1
    managed: bool
    endpoint_live: bool
    endpoint_ready: bool
    endpoint_checked_at: datetime | None = None
    deployment: DeploymentState | None = None
    pods: list[PodState] = Field(default_factory=list)
    message: str | None = None


class PodLogStream(BaseModel):
    pod_uid: str
    pod_name: str
    container: str
    previous: bool
    text: str = ""
    error: str | None = None
    truncated: bool = False


class PodLogs(BaseModel):
    schema_version: Literal[1] = 1
    streams: list[PodLogStream] = Field(default_factory=list)
    truncated: bool = False
    min_refresh_seconds: int = 5
    ordering: Literal["per-container; no global clock ordering"] = "per-container; no global clock ordering"
