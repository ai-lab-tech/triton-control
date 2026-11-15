"""Installation build policy. Validation is pure and never reads credentials."""

import hashlib
import json
import re
from ipaddress import ip_network
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BuildPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    executor_image: str = Field(pattern=r"^[a-z0-9./_-]+@sha256:[a-f0-9]{64}$")
    release: str = Field(pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")
    namespace: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    platform: Literal["linux/amd64", "linux/arm64"] = "linux/amd64"
    s3_profile_name: str = Field(min_length=1, max_length=128)
    staging_prefix: str = Field(pattern=r"^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*/$")
    allowed_repositories: tuple[str, ...] = Field(min_length=1)
    base_pull_secret: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    push_secret: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    runtime_pull_secret: str = Field(pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    provisioning_revision: str = Field(min_length=1, max_length=128)
    max_context_bytes: int = Field(default=10485760, ge=1024, le=1073741824)
    cpu_millis: int = Field(default=1000, ge=100, le=64000)
    memory_mib: int = Field(default=1024, ge=128, le=131072)
    timeout_seconds: int = Field(default=300, ge=10, le=7200)
    egress_cidrs: tuple[str, ...] = Field(min_length=1)
    egress_ports: tuple[int, ...] = (443,)
    capabilities: tuple[Literal["CHOWN", "DAC_OVERRIDE", "FOWNER", "SETGID", "SETUID"], ...] = ("CHOWN", "FOWNER")
    run_as_user: Literal[0] = 0
    seccomp_profile: Literal["RuntimeDefault"] = "RuntimeDefault"

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if len({self.base_pull_secret, self.push_secret, self.runtime_pull_secret}) != 3:
            raise ValueError("Base pull, build push and runtime pull require distinct credential references")
        for repo in self.allowed_repositories:
            if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+", repo) or ".." in repo:
                raise ValueError("Allowlist entries must be exact registry/repository names, without tags or wildcards")
        for cidr in self.egress_cidrs:
            if ip_network(cidr).prefixlen == 0:
                raise ValueError("Unrestricted egress is not a qualification policy")
        if not self.egress_ports or any(port < 1 or port > 65535 for port in self.egress_ports):
            raise ValueError("Explicit valid egress ports required")
        return self

    def fingerprint(self) -> str:
        # Enabling an already qualified policy is not a change to its execution contract.
        data = self.model_dump(mode="json", exclude={"enabled"})
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def permits_destination(self, destination: str) -> bool:
        repository, separator, tag = destination.rpartition(":")
        return bool(
            separator
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag)
            and repository in self.allowed_repositories
        )
