"""Pure installation capability response; no configuration I/O."""

from typing import Literal

from pydantic import BaseModel

Capability = Literal[
    "serving_read", "artifact_read", "interactive_inference", "development_tools", "development_jobs",
    "model_write", "artifact_write", "direct_deployment", "identity_admin", "credential_admin", "trust_admin",
    "production_changes",
]


class Capabilities(BaseModel):
    mode: Literal["development", "production"]
    capabilities: dict[Capability, bool]
    explanation: str
