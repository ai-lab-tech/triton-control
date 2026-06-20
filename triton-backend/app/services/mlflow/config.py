"""Environment-backed configuration for embedded singleton MLflow."""

from __future__ import annotations

import os


def base_path() -> str:
    """Return normalized proxy base path used by frontend iframe embedding."""
    raw = os.getenv("MLFLOW_BASE_PATH", "/api/mlflow/proxy/")
    path = f"/{(raw or '').strip().strip('/')}/"
    return path if path != "//" else "/api/mlflow/proxy/"
