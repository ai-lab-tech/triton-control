"""Validated defaults for the health probes of generated Triton serving pods."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HTTPProbeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str = Field(pattern=r"^/\S*$")
    initialDelaySeconds: int = Field(ge=0)
    periodSeconds: int = Field(default=10, ge=1)
    timeoutSeconds: int = Field(default=1, ge=1)
    failureThreshold: int = Field(ge=1)


class ServingProbeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    startup: HTTPProbeSettings = Field(
        default_factory=lambda: HTTPProbeSettings(
            path="/v2/health/ready",
            initialDelaySeconds=0,
            failureThreshold=90,
        )
    )
    liveness: HTTPProbeSettings = Field(
        default_factory=lambda: HTTPProbeSettings(
            path="/v2/health/live",
            initialDelaySeconds=0,
            failureThreshold=6,
        )
    )
    readiness: HTTPProbeSettings = Field(
        default_factory=lambda: HTTPProbeSettings(
            path="/v2/health/ready",
            initialDelaySeconds=0,
            failureThreshold=12,
        )
    )


def serving_probes() -> dict[str, dict[str, Any]]:
    """Read Helm-supplied JSON, retaining the same defaults for non-Helm users."""
    raw = os.environ.get("TRITON_DEPLOY_PROBES_JSON")
    settings = ServingProbeSettings.model_validate_json(raw) if raw else ServingProbeSettings()
    probes: dict[str, dict[str, Any]] = {}
    for kind, probe in (
        ("startup", settings.startup),
        ("liveness", settings.liveness),
        ("readiness", settings.readiness),
    ):
        probes[f"{kind}Probe"] = {
            "httpGet": {"path": probe.path, "port": 8000},
            **probe.model_dump(exclude={"path"}),
        }
    return probes
