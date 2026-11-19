"""Installation-local allowlists. No credentials in policy or admission results."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from typing_extensions import Self

from app.schemas.serving_metadata import (
    Count,
    Cpu,
    GitLocation,
    Memory,
    MetadataSource,
    Name,
    Path,
    S3Location,
    ServingRelease,
    ServingTarget,
    Uri,
)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class S3Binding(Contract):
    provider: Literal["s3"]
    profile_id: Annotated[int, Field(strict=True, gt=0)]
    # Pin the non-secret scope so editing a global profile cannot silently rebind it.
    endpoint: Annotated[str, Field(max_length=2048, pattern=r"^https?://[^\s@?#]+$")]
    bucket: Annotated[str, Field(min_length=3, max_length=63)]
    prefix: Path | Literal[""] = ""


class CredentialBinding(Contract):
    provider: Literal["git", "registry"]
    credential: Name


Binding = Annotated[S3Binding | CredentialBinding, Field(discriminator="provider")]


class Capacity(Contract):
    cpu: Cpu
    memory: Memory
    gpu: Count


class TargetGrant(Contract):
    source_ids: tuple[Name, ...] = Field(min_length=1, max_length=100)
    user_ids: tuple[Annotated[int, Field(strict=True, gt=0)], ...] = Field(default=(), max_length=1000)
    model_storage: tuple[Name, ...] = Field(min_length=1, max_length=100)
    registry_pull: tuple[Name, ...] = Field(min_length=1, max_length=100)
    hardware: tuple[Name, ...] = Field(min_length=1, max_length=100)
    per_replica: Capacity
    total: Capacity
    max_replicas: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]


class SourceGrant(Contract):
    location: Annotated[GitLocation | S3Location, Field(discriminator="type")]
    read_binding: Name
    user_ids: tuple[Annotated[int, Field(strict=True, gt=0)], ...] = Field(default=(), max_length=1000)


class Hardware(Contract):
    architecture: Literal["amd64", "arm64"]
    accelerator: Name | None = None
    max_gpu_per_replica: Count = 0

    @model_validator(mode="after")
    def accelerator_required(self) -> Self:
        if self.max_gpu_per_replica and not self.accelerator:
            raise ValueError("GPU capacity requires an accelerator")
        return self


class BindingPolicy(Contract):
    schema_version: Literal[1] = 1
    source_read: dict[Name, Binding] = Field(default_factory=dict, max_length=100)
    model_storage: dict[Name, S3Binding] = Field(default_factory=dict, max_length=100)
    registry_pull: dict[Name, CredentialBinding] = Field(default_factory=dict, max_length=100)
    build_push: dict[Name, CredentialBinding] = Field(default_factory=dict, max_length=100)
    sources: dict[Name, SourceGrant] = Field(default_factory=dict, max_length=100)
    targets: dict[Name, TargetGrant] = Field(default_factory=dict, max_length=100)
    hardware: dict[Name, Hardware] = Field(default_factory=dict, max_length=100)
    build_user_ids: tuple[Annotated[int, Field(strict=True, gt=0)], ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def references(self) -> Self:
        if any(b.provider != "registry" for b in (*self.registry_pull.values(), *self.build_push.values())):
            raise ValueError("Registry purpose requires registry credentials")
        if any(isinstance(b, CredentialBinding) and b.provider != "git" for b in self.source_read.values()):
            raise ValueError("Source reads require S3 or Git")
        for source in self.sources.values():
            binding = self.source_read.get(source.read_binding)
            if binding is None or binding.provider != source.location.type:
                raise ValueError("Source binding missing or wrong provider")
        for name, target in self.targets.items():
            if not (
                set(target.source_ids) <= self.sources.keys()
                and set(target.model_storage) <= self.model_storage.keys()
                and set(target.registry_pull) <= self.registry_pull.keys()
                and set(target.hardware) <= self.hardware.keys()
            ):
                raise ValueError("Target references an unknown local grant")
        return self


class GitCredential(Contract):
    provider: Literal["git"]
    repository: Uri
    username: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\s:/@]+$")]
    password: SecretStr = Field(min_length=1, max_length=16384)
    read_only: Literal[True]

    @model_validator(mode="after")
    def https_only(self) -> Self:
        if not self.repository.startswith("https://"):
            raise ValueError("Git requires HTTPS")
        return self


class RegistryCredential(Contract):
    provider: Literal["registry"]
    registry: Annotated[str, Field(max_length=253, pattern=r"^[a-z0-9][a-z0-9.-]*(:[1-9][0-9]{0,4})?$")]
    repository_prefix: Path
    username: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[^\s:/@]+$")]
    password: SecretStr = Field(min_length=1, max_length=16384)
    permission: Literal["pull", "push"]


CredentialInput = Annotated[GitCredential | RegistryCredential, Field(discriminator="provider")]


class PolicyUpdate(Contract):
    expected_revision: Annotated[int, Field(strict=True, ge=0)]
    policy: BindingPolicy


class PolicyResponse(Contract):
    revision: int
    policy: BindingPolicy


class CredentialResponse(Contract):
    name: Name
    provider: Literal["git", "registry"]
    configured: bool


class AdmissionRequest(Contract):
    source: MetadataSource
    target: ServingTarget
    release: ServingRelease
    hardware: Name


class AdmissionResult(Contract):
    policy_revision: int
    policy_digest: str
    source_id: Name
    target_id: Name
    release_digest: str
    hardware: Hardware
    peak_replicas: int
    # Names only: safe to retain as audit evidence, never an execution permit.
    bindings: dict[str, str]
