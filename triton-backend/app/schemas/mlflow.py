"""Schemas for singleton MLflow installation and embedded proxy status."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import field_validator
from sqlmodel import SQLModel


class InstallMlflowRequest(SQLModel):
    """Request body for ``POST /api/mlflow``."""

    installation_name: str = "mlflow"
    image: str = "ghcr.io/mlflow/mlflow:v2.15.1"
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
        return normalized[:63].strip("-") or "mlflow"

    @field_validator("dockerconfigjson")
    @classmethod
    def normalize_dockerconfigjson(cls, value: str | None) -> str | None:
        cleaned = (value or "").strip()
        return cleaned or None


class MlflowInstallResponse(SQLModel):
    """Response after MLflow Kubernetes resources are applied."""

    namespace: str
    deployment_name: str
    service_name: str
    image: str
    applied_resources: List[str]


class MlflowStatusResponse(SQLModel):
    """Response describing singleton MLflow installation and UI proxy base path."""

    installed: bool
    status: str = "not_installed"
    ready: bool = False
    status_message: str = ""
    base_path: str = "/api/mlflow/proxy/"
    service_url: str = ""
    installation: Optional[MlflowInstallResponse] = None


class MlflowDeleteResponse(SQLModel):
    """Response after uninstalling singleton MLflow workload."""

    status: str
    message: str
    namespace: str


class MlflowProxyStatusResponse(SQLModel):
    """Response describing proxy availability for embedded MLflow iframe."""

    ready: bool
    status: str
    status_message: str
    base_path: str
    installed: bool
    namespace: str = ""
    service_name: str = ""
    deployment_name: str = ""
    last_transition_at: Optional[datetime] = None
