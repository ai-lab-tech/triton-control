"""Schemas for installing a Triton Perf Analyzer container on Kubernetes.

Defines the request and response DTOs consumed by the Perf Analyzer install API.
Pure schema module - no service, repository, or HTTP imports.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import field_validator
from sqlmodel import Field, SQLModel


class InstallPerfAnalyzerRequest(SQLModel):
    """Request body for ``POST /api/perf-analyzers``."""

    installation_name: str = "perf-analyzer"
    image: str
    dockerconfigjson: Optional[str] = None

    @field_validator("installation_name", "image")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    @field_validator("installation_name")
    @classmethod
    def normalize_installation_name(cls, value: str) -> str:
        if not re.search(r"[a-zA-Z0-9]", value):
            raise ValueError("installation_name must contain letters or numbers")
        normalized = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", value.lower())).strip("-")
        return normalized[:63].strip("-") or "perf-analyzer"

    @field_validator("dockerconfigjson")
    @classmethod
    def normalize_dockerconfigjson(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class PerfAnalyzerInstallResponse(SQLModel):
    """Response after Perf Analyzer Kubernetes resources are applied."""

    namespace: str
    deployment_name: str
    image: str
    applied_resources: List[str]


class PerfAnalyzerStatusResponse(SQLModel):
    """Response describing the singleton Perf Analyzer installation state."""

    installed: bool
    status: str = "not_installed"
    ready: bool = False
    status_message: str = ""
    installation: Optional[PerfAnalyzerInstallResponse] = None


class PerfAnalyzerDeleteResponse(SQLModel):
    """Response after uninstalling the singleton Perf Analyzer workload."""

    status: str
    message: str
    namespace: str


class RunPerfAnalyzerRequest(SQLModel):
    """Request body for a model-level Perf Analyzer run."""

    instance_id: int = Field(gt=0)
    model_name: str
    model_version: str
    batch_size: int = Field(default=1, gt=0)
    concurrency_range: str = "1"
    measurement_request_count: int = Field(default=50, gt=0)
    input_data: Optional[str] = None

    @field_validator("model_name", "model_version", "concurrency_range")
    @classmethod
    def strip_run_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    @field_validator("concurrency_range")
    @classmethod
    def validate_concurrency_range(cls, value: str) -> str:
        if not re.fullmatch(r"[1-9][0-9]*(?::[0-9]+(?::[1-9][0-9]*)?)?", value):
            raise ValueError("concurrency_range must use start, start:end, or start:end:step")
        return value


class PerfAnalyzerRunResponse(SQLModel):
    """Output captured from a Perf Analyzer process executed in Kubernetes."""

    command: List[str]
    output: str


class PerfAnalyzerLatestRunResponse(SQLModel):
    """Persisted latest Perf Analyzer result for one instance model target."""

    found: bool
    executed_at: Optional[datetime] = None
    batch_size: Optional[int] = None
    concurrency_range: Optional[str] = None
    measurement_request_count: Optional[int] = None
    input_data: Optional[str] = None
    command: List[str] = Field(default_factory=list)
    output: str = ""
