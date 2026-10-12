"""Model-scoped asynchronous performance API contracts."""

import json
from datetime import datetime
from typing import Literal

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.schemas.perf_analyzer import PerfAnalyzerLatestRunResponse, RunPerfAnalyzerRequest

PerfState = Literal["creating", "pending", "running", "stopping", "succeeded", "failed", "cancelled"]


class StartModelPerfRequest(SQLModel):
    model_version: str
    image: str | None = None
    dockerconfigjson: str | None = Field(default=None, repr=False, max_length=500_000)
    batch_size: int = Field(default=1, gt=0)
    concurrency_range: str = "1"
    measurement_request_count: int = Field(default=50, gt=0)
    input_data: str | None = Field(default=None, max_length=500_000)

    @field_validator("model_version", "concurrency_range")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return RunPerfAnalyzerRequest.strip_run_text(value)

    @field_validator("concurrency_range")
    @classmethod
    def validate_concurrency(cls, value: str) -> str:
        return RunPerfAnalyzerRequest.validate_concurrency_range(value)

    @field_validator("image")
    @classmethod
    def validate_image(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or any(c.isspace() for c in value.strip())):
            raise ValueError("image must be a non-empty container image reference")
        return value.strip() if value else None

    @field_validator("dockerconfigjson")
    @classmethod
    def validate_registry(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        try:
            parsed = json.loads(value)
            if not isinstance(parsed, dict) or not isinstance(parsed.get("auths"), dict):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Registry configuration must be a Docker config JSON with an auths object") from None
        return value


class ModelPerfRunResponse(SQLModel):
    id: str
    instance_id: int
    model_name: str
    model_version: str
    image: str
    state: PerfState
    message: str
    batch_size: int
    concurrency_range: str
    measurement_request_count: int
    input_data: str | None = None
    command: list[str] = Field(default_factory=list)
    output: str = ""
    exit_code: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ModelPerfStatusResponse(SQLModel):
    active_run: ModelPerfRunResponse | None = None
    latest_run: ModelPerfRunResponse | None = None
    latest_result: PerfAnalyzerLatestRunResponse
    default_image: str
