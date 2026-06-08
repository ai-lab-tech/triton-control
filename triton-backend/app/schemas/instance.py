"""Pydantic/SQLModel schemas for Triton instance management.

Defines DTOs and request models used by the instance and model APIs:
  ``TritonInstanceDTO``             — full instance response (includes nested
                                       ``InstanceS3ConfigDTO``).
  ``InstanceS3ConfigDTO``           — S3 sub-configuration embedded in the
                                       instance DTO.
  ``CreateTritonInstanceRequest``   — request body for ``POST /api/instances``;
                                       ``url`` is normalised by
                                       ``_normalize_triton_url`` (adds
                                       ``http://`` and strips trailing slash).
  ``UpdateTritonInstanceRequest``   — request body for ``PUT /api/instances/{id}``;
                                       updates the Triton endpoint and TLS
                                       verification settings.
  ``UpdateInstanceS3Request``       — request body for ``PUT /{id}/s3``.
  ``RegisterTritonInstanceResponse``— lightweight response after registration.
  ``TritonRepositoryModelDTO``      — single model entry from the repository index.
  ``ModelRepositoryActionResponse`` — result of a load/unload action.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional
from urllib.parse import urlparse, urlunparse

from pydantic import field_validator
from sqlmodel import Field, SQLModel


def _normalize_triton_url(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    if not (v.startswith("http://") or v.startswith("https://")):
        v = f"https://{v}"
    return v.rstrip("/")


def _normalize_metrics_url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not (v.startswith("http://") or v.startswith("https://")):
        v = f"https://{v}"
    parsed = urlparse(v)
    path = parsed.path.rstrip("/")
    if not path:
        parsed = parsed._replace(path="/metrics")
        return urlunparse(parsed)
    parsed = parsed._replace(path=path)
    return urlunparse(parsed)


class InstanceS3ConfigDTO(SQLModel):
    enabled: bool = False
    endpoint: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    prefix: Optional[str] = None
    access_key: Optional[str] = None
    secret_configured: bool = False
    use_https: Optional[bool] = None
    verify_ssl: Optional[bool] = None
    ca_certificate: str = ""
    address_style: Optional[Literal["path", "virtual"]] = None


class UpdateInstanceS3Request(SQLModel):
    enabled: bool = False
    endpoint: Optional[str] = None
    bucket: Optional[str] = None
    region: Optional[str] = None
    prefix: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    use_https: Optional[bool] = None
    verify_ssl: Optional[bool] = None
    ca_certificate: str = ""
    address_style: Optional[Literal["path", "virtual"]] = None


class TritonRepositoryModelDTO(SQLModel):
    name: str
    version: Optional[str] = None
    state: Optional[str] = None
    reason: Optional[str] = None


class TritonInstanceDTO(SQLModel):
    """DTO for outgoing Triton instance data (API responses)."""

    id: int
    url: str
    name: str
    model_names: List[str]
    repository_models: List[TritonRepositoryModelDTO] = Field(default_factory=list)
    server_metadata: Optional[dict[str, Any]] = None
    health_live: bool = False
    health_ready: bool = False
    health_last_checked_at: Optional[datetime] = None
    health_error: Optional[str] = None
    triton_verify_ssl: bool = False
    triton_ca_certificate: str = ""
    metrics_url: Optional[str] = None
    metrics_cpu: float = 0
    metrics_ram: float = 0
    metrics_gpu: float = 0
    metrics_last_checked_at: Optional[datetime] = None
    metrics_error: Optional[str] = None
    deployment_runtime: str = "external"
    deployment_namespace: Optional[str] = None
    deployment_name: Optional[str] = None
    deployment_service_name: Optional[str] = None
    deployment_secret_name: Optional[str] = None
    deployment_log: str = ""
    is_self_deployed: bool = False
    pod_statuses: List[str] = Field(default_factory=list)
    created_at: datetime
    s3: Optional[InstanceS3ConfigDTO] = None


class InstanceLogsResponse(SQLModel):
    logs: str


class ModelRepositoryActionResponse(SQLModel):
    status: str
    message: Optional[str] = None


class CreateTritonInstanceRequest(SQLModel):
    """Request to create a Triton instance."""

    url: str
    name: Optional[str] = None
    verify_ssl: bool = False
    ca_certificate: str = ""
    metrics_url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def normalize_url(cls, v: str) -> str:
        result = _normalize_triton_url(v)
        if not result:
            raise ValueError("url must not be empty")
        return result

    @field_validator("ca_certificate", mode="before")
    @classmethod
    def strip_ca_certificate(cls, v: Any) -> str:
        return (str(v) if v else "").strip()

    @field_validator("metrics_url")
    @classmethod
    def normalize_metrics_url(cls, v: str | None) -> str | None:
        return _normalize_metrics_url(v)


class UpdateTritonInstanceRequest(SQLModel):
    """Request to update an existing Triton instance connection."""

    url: str
    verify_ssl: bool = False
    ca_certificate: str = ""
    metrics_url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def normalize_url(cls, v: str) -> str:
        result = _normalize_triton_url(v)
        if not result:
            raise ValueError("url must not be empty")
        return result

    @field_validator("ca_certificate", mode="before")
    @classmethod
    def strip_ca_certificate(cls, v: Any) -> str:
        return (str(v) if v else "").strip()

    @field_validator("metrics_url")
    @classmethod
    def normalize_metrics_url(cls, v: str | None) -> str | None:
        return _normalize_metrics_url(v)


class RegisterTritonInstanceResponse(SQLModel):
    """Response after registering a Triton instance."""

    id: int
    url: str
    name: str
    model_names: List[str]
    created_at: datetime
