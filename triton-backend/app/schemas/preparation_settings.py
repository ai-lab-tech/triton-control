"""Installation limits for model capture and verified workspace inputs."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreparationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_object_gib: int = Field(default=10, strict=True, ge=1, le=4096)
    max_repository_gib: int = Field(default=15, strict=True, ge=1, le=4096)
    max_workspace_gib: int = Field(default=15, strict=True, ge=1, le=8192)
    capture_timeout_seconds: int = Field(default=3600, strict=True, ge=60, le=604800)
    download_timeout_seconds: int = Field(default=3600, strict=True, ge=60, le=604800)

    @model_validator(mode="after")
    def consistent_limits(self) -> Self:
        if not self.max_object_gib <= self.max_repository_gib <= self.max_workspace_gib:
            raise ValueError("Require object limit <= repository limit <= workspace input limit")
        return self


class PreparationSettings(BaseModel):
    limits: PreparationLimits
    defaults: PreparationLimits
    source: Literal["deployment", "admin"]
