"""Pydantic/SQLModel schemas for Kubernetes Triton deployments.

Defines the public DTOs used by the deployment API:
  ``CreateDeploymentRequest`` - minimal UI payload for deploying Triton
                                against an S3 model repository.
  ``DeploymentResponse``      - Kubernetes object names applied by the
                                backend. Secrets are never echoed back.

Pure schema module - no service, repository, or HTTP imports.
"""

from __future__ import annotations

import re
from typing import Any, List, Literal, Optional
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from pydantic import field_validator, model_validator
from sqlmodel import Field, SQLModel


class CreateDeploymentRequest(SQLModel):
    """Request body for ``POST /api/deployments``."""

    deployment_name: str
    s3_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "us-east-1"
    s3_ca_certificate: Optional[str] = None
    image: str
    dockerconfigjson: Optional[str] = None
    ingress_host: Optional[str] = None
    ingress_scheme: Optional[Literal["http", "https"]] = None
    ingress_class_name: Optional[str] = None
    model_control_mode: Literal["explicit", "poll"] = "explicit"
    repository_poll_secs: int = Field(default=15, gt=0)
    model_name: Optional[str] = None
    allow_metrics: bool = True
    requirements_txt: Optional[str] = None
    gpu_count: Optional[int] = None
    cpu: Optional[str] = None
    cpu_limit: Optional[str] = None
    memory: Optional[str] = None
    memory_limit: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_ingress_url(cls, data: Any) -> Any:
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

    @field_validator("deployment_name", "s3_url", "s3_access_key", "s3_secret_key", "s3_region", "image")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("value must not be empty")
        return cleaned

    @field_validator("deployment_name")
    @classmethod
    def normalize_deployment_name(cls, value: str) -> str:
        if not re.search(r"[a-zA-Z0-9]", value):
            raise ValueError("deployment_name must contain letters or numbers")
        normalized = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", value.lower())).strip("-")
        return normalized[:63].strip("-") or "triton"

    @field_validator("s3_url")
    @classmethod
    def validate_s3_url(cls, value: str) -> str:
        normalized = value
        if normalized.startswith("http://") or normalized.startswith("https://"):
            normalized = f"s3://{normalized}"
        if not normalized.startswith("s3://"):
            raise ValueError("s3_url must start with s3:// (or use http:// / https:// endpoint form)")
        return normalized.rstrip()

    @field_validator("model_name", "dockerconfigjson")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    @field_validator("s3_ca_certificate")
    @classmethod
    def normalize_optional_certificate(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None

    @field_validator("ingress_host")
    @classmethod
    def normalize_ingress_host(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip().lower().rstrip(".")
        if not cleaned:
            return None
        if "/" in cleaned or "://" in cleaned or not re.fullmatch(r"[a-z0-9]([a-z0-9.-]*[a-z0-9])?", cleaned):
            raise ValueError("ingress_host must be a DNS host name or http(s) URL")
        return cleaned

    @field_validator("requirements_txt")
    @classmethod
    def normalize_requirements_txt(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        error = _validate_requirements_txt(cleaned)
        if error:
            raise ValueError(error)
        return cleaned

def _validate_requirements_txt(value: str) -> str:
    for index, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        error = _validate_requirement_line(line)
        if error:
            return f"Line {index}: {error}"
    return ""


def _validate_requirement_line(line: str) -> str:
    requirement = line.split(" #", 1)[0].strip()
    try:
        Requirement(requirement)
    except InvalidRequirement as exc:
        return str(exc)
    return ""


class DeploymentResponse(SQLModel):
    """Response after Kubernetes resources are applied."""

    instance_id: int
    namespace: str
    deployment_name: str
    service_name: str
    secret_name: str
    image: str
    s3_url: str
    applied_resources: List[str]


class DeploymentDeleteResponse(SQLModel):
    """Response after deleting a Kubernetes-managed Triton deployment."""

    status: str
    message: str
    namespace: str
