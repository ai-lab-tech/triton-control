"""Development deployment model state and replica controls."""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.lineage import Digest
from app.schemas.serving_metadata import Contract, Image, Repository, Resources, StartupModel


class StartupSpec(Contract):
    image: Image
    repository: Repository
    models: tuple[StartupModel, ...] = Field(min_length=1, max_length=100)
    resources_per_replica: Resources


class DevelopmentStartupRelease(Contract):
    """The startup template input for a local development deployment."""

    api_version: Literal["serving.triton-control/v1alpha1"]
    kind: Literal["DevelopmentStartupRelease"]
    spec: StartupSpec


class DeclareModelState(BaseModel):
    release_digest: Digest


class ScaleModelState(BaseModel):
    replicas: int = Field(strict=True, ge=1, le=100)


class ModelState(BaseModel):
    capacity_owner: Literal["manual", "source", "autoscaler"]
    mode: Literal["interactive", "declared"]
    replicas: int
    release_digest: Digest | None = None
    pending_replicas: int | None = None
    rollout_complete: bool
    interactive_allowed: bool
    can_declare: bool
    can_restore: bool
    can_scale_above_one: bool
