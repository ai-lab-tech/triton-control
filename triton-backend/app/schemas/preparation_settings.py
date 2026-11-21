"""Installation ceilings for model preparation and workspace allocation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PreparationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_input_gib: int = Field(default=15, strict=True, ge=1, le=8192)
    max_workspace_storage_gib: int = Field(default=50, strict=True, ge=6, le=16384)
    transfer_timeout_seconds: int = Field(default=3600, strict=True, ge=60, le=604800)
    require_s3_versioning: bool = Field(default=True, strict=True)


class PreparationSettings(BaseModel):
    limits: PreparationLimits
    defaults: PreparationLimits
    source: Literal["deployment", "admin"]
