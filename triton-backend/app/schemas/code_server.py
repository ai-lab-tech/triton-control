"""Schemas for per-user Kubernetes code-server workspaces."""

from __future__ import annotations

import re
from typing import List, Literal, Optional
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel


class CreateCodeServerRequest(SQLModel):
    """Request body for creating or replacing the caller's code-server workspace."""

    name: str = "workspace"
    image: str = "nvcr.io/nvidia/tritonserver:25.02-py3"
    password: str = Field(min_length=8, max_length=256)
    ingress_host: Optional[str] = None
    ingress_scheme: Optional[Literal["http", "https"]] = None
    ingress_class_name: Optional[str] = None
    storage_size: str = "20Gi"
    cpu: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory: Optional[str] = None
    memory_limit: Optional[str] = None
    dockerconfigjson: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_ingress_url(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw_host = (data.get("ingress_host") or "").strip()
        if not raw_host:
            return data
        parsed = urlsplit(raw_host)
        if not parsed.scheme:
            return data
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ingress_host must be a DNS host name or http(s) URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("ingress_host URL must not include a path, query, or fragment")
        normalized = dict(data)
        normalized["ingress_host"] = parsed.hostname
        normalized["ingress_scheme"] = parsed.scheme
        return normalized

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

    @field_validator("ingress_host")
    @classmethod
    def normalize_ingress_host(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip().lower().rstrip(".")
        if not cleaned:
            return None
        if "/" in cleaned or "://" in cleaned or not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", cleaned):
            raise ValueError("ingress_host must be a DNS host name or http(s) URL")
        return cleaned

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
    password: str
    status: str
    status_message: str
    applied_resources: List[str]


class CodeServerDeleteResponse(SQLModel):
    """Response after deleting a code-server workload record."""

    status: str
    message: str
    namespace: str
