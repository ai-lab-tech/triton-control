"""Installation build policy. Validation is pure and never reads credentials."""

import hashlib
import json
import re
from ipaddress import ip_network
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from app.schemas.registry import RegistryUpdate


def validate_repositories(repositories: tuple[str, ...]) -> None:
    for repo in repositories:
        if not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+", repo) or ".." in repo:
            raise ValueError("Use exact registry/repository names without tags or wildcards")


class BuildDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    push_secret: str | None = Field(default=None, max_length=253, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    namespace: str = Field(max_length=63, pattern=r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
    allowed_repositories: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        validate_repositories(self.allowed_repositories)
        return self


class BuildDestinationUpdate(BuildDestination):
    reuse_pull_credentials: bool = False
    dockerconfigjson: str | None = Field(default=None, max_length=500_000, repr=False)

    @model_validator(mode="after")
    def validate_credentials(self) -> Self:
        if self.reuse_pull_credentials and (self.dockerconfigjson or self.push_secret):
            raise ValueError("Choose saved pull credentials or separate push credentials")
        if self.dockerconfigjson:
            RegistryUpdate(dockerconfigjson=self.dockerconfigjson)
            if self.push_secret:
                raise ValueError("Provide either Docker credentials or an existing secret reference")
        return self


class BuilderImage(BaseModel):
    executor_image: str = Field(
        default=("ghcr.io/osscontainertools/kaniko@sha256:"
                 "e7cba1f7e70c01c8ee1cbe622d07990c04dea550430c4c81319d26679226cd37"),
        pattern=r"^[a-z0-9./_-]+@sha256:[a-f0-9]{64}$",
    )
    release: str = Field(default="v1.29.1", pattern=r"^v[0-9]+\.[0-9]+\.[0-9]+$")


class BuildDestinationSettings(BaseModel):
    builder: BuilderImage = Field(default_factory=BuilderImage)
    reuse_pull_credentials: bool = False
    push_credentials_saved: bool = False
    destination: BuildDestination | None
    has_override: bool
    status: str


class BuildPolicy(BuilderImage):
    model_config = ConfigDict(extra="forbid", frozen=True)

    anonymous_push: bool = False
    enabled: bool = False
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
        if ("executor_image" in self.model_fields_set) != ("release" in self.model_fields_set):
            raise ValueError("Specify both executor_image and release when overriding the default builder")
        if len({self.base_pull_secret, self.push_secret, self.runtime_pull_secret}) != 3:
            raise ValueError("Base pull, build push and runtime pull require distinct credential references")
        validate_repositories(self.allowed_repositories)
        for cidr in self.egress_cidrs:
            if ip_network(cidr).prefixlen == 0:
                raise ValueError("Unrestricted egress is not a qualification policy")
        if not self.egress_ports or any(port < 1 or port > 65535 for port in self.egress_ports):
            raise ValueError("Explicit valid egress ports required")
        return self

    def fingerprint(self) -> str:
        # Enabling an already qualified policy is not a change to its execution contract.
        data = self.model_dump(mode="json", exclude={"enabled"})
        if not self.anonymous_push:
            data.pop("anonymous_push")  # Preserve existing qualification fingerprints.
        return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def permits_repository(self, repository: str) -> bool:
        return repository in self.allowed_repositories

    def permits_destination(self, destination: str) -> bool:
        repository, separator, tag = destination.rpartition(":")
        return bool(
            separator
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag)
            and self.permits_repository(repository)
        )


class ManagedRegistryPolicy(BuildPolicy):
    """Registry output from the installation-managed development builder."""

    managed_registry: Literal[True] = True


class LocalArchivePolicy(BuildPolicy):
    """Installation-generated development contract; never accepted by the policy settings API."""

    local_archive: Literal[True] = True

    def permits_repository(self, repository: str) -> bool:
        component = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
        return bool(re.fullmatch(rf"(?:[a-z0-9.-]+:[0-9]+/)?{component}(?:/{component})*", repository))


class BuildPolicySettings(BaseModel):
    policy: BuildPolicy | None
    source: Literal["admin", "deployment", "unconfigured"]
