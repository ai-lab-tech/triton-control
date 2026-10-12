"""Environment-backed configuration for embedded singleton MLflow."""

from __future__ import annotations

import os


def server_version() -> str:
    """Return the MLflow version configured by the deployment operator."""
    return os.getenv("MLFLOW_VERSION", "3.14.0").strip()


def server_image() -> str:
    """Use the operator-configured MLflow version and image repository."""
    repository = os.getenv("MLFLOW_IMAGE_REPOSITORY", "ghcr.io/mlflow/mlflow").strip()
    version = server_version()
    return f"{repository}:v{version}"


def base_path() -> str:
    """Return normalized proxy base path used by frontend iframe embedding."""
    raw = os.getenv("MLFLOW_BASE_PATH", "/api/mlflow/proxy/")
    path = f"/{(raw or '').strip().strip('/')}/"
    return path if path != "//" else "/api/mlflow/proxy/"
