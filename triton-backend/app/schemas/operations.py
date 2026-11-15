"""Pure operation contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class OperationRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    target: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)


class OperationResponse(BaseModel):
    id: str
    kind: str
    target: str
    state: str
    cancel_requested: bool
    attempts: int
    created_at: datetime
    updated_at: datetime
    result: dict[str, Any]
    message: str
    status_url: str


class ReconcileResult(BaseModel):
    state: Literal["pending", "succeeded", "failed", "cancelled"] = "pending"
    result: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    retry_after: int = Field(default=5, ge=1, le=3600)
