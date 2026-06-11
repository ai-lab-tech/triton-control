"""Schemas for per-user Kubernetes code-server workspaces."""

from __future__ import annotations

import re
from typing import List, Literal, Optional

from pydantic import field_validator
from sqlmodel import SQLModel


class CreateCodeServerRequest(SQLModel):
    """Request body for creating the caller's code-server workspace."""

    name: str = "workspace"
    image: str = "nvcr.io/nvidia/tritonserver:25.02-py3"
    theme: Literal[
        "Default Dark+",
        "Default Light+",
        "Quiet Light",
        "Monokai",
        "Solarized Dark",
    ] = "Default Dark+"
    storage_size: str = "20Gi"
    cpu: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory: Optional[str] = None
    memory_limit: Optional[str] = None
    dockerconfigjson: Optional[str] = None

    @field_validator("name", "image", "storage_size")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not re.search(r"[a-zA-Z0-9]", value):
            raise ValueError("name must contain letters or numbers")
        normalized = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", value.lower())).strip("-")
        return normalized[:40].strip("-") or "workspace"

    @field_validator("dockerconfigjson", "cpu", "cpu_limit", "memory", "memory_limit")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class CodeServerDTO(SQLModel):
    """Returned code-server workspace owned by the authenticated user."""

    id: int
    name: str
    namespace: str
    statefulset_name: str
    service_name: str
    image: str
    url: str
    status: str
    status_message: str
    applied_resources: List[str]


class CodeServerDeleteResponse(SQLModel):
    """Response after deleting a code-server workload record."""

    status: str
    message: str
    namespace: str
