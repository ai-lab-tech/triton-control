"""S08-A wire contracts. Pure validation; no fetching, credentials or approval."""

import re
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, model_validator
from pydantic.alias_generators import to_camel
from typing_extensions import Self

from app.schemas.lineage import Digest, ReleaseLineageV1

API_VERSION = "serving.triton-control/v1alpha1"
Name = Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")]
Image = Annotated[
    str,
    Field(max_length=512, pattern=r"^[a-z0-9][a-z0-9.:-]*(/[a-z0-9][a-z0-9._-]*)+@sha256:[0-9a-f]{64}$"),
]
Count = Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
PositiveCount = Annotated[int, Field(strict=True, ge=1, le=1_000_000)]
RelativePath = Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$")]


def safe_path(value: str) -> str:
    if any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("Relative path contains traversal")
    return value


Path = Annotated[RelativePath, AfterValidator(safe_path)]


def safe_uri(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"s3", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "%" in value
        or "\\" in value
        or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in value)
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or "//" in parsed.path
    ):
        raise ValueError("Expected a credential-free S3 or HTTPS URI")
    if parsed.port is not None and parsed.scheme == "s3":
        raise ValueError("S3 URI must not specify a port")
    if parsed.scheme == "s3" and not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", parsed.netloc):
        raise ValueError("Invalid S3 bucket")
    return value


Uri = Annotated[
    str,
    Field(min_length=1, max_length=2048, pattern=r"^(s3|https)://[A-Za-z0-9][A-Za-z0-9./:_-]*$"),
    AfterValidator(safe_uri),
]
Cpu = Annotated[str, Field(max_length=20, pattern=r"^((0|[1-9][0-9]*)(\.[0-9]{1,3})?|[1-9][0-9]*m)$")]
Memory = Annotated[str, Field(max_length=24, pattern=r"^[1-9][0-9]*(Ki|Mi|Gi|Ti|k|M|G|T)?$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, alias_generator=to_camel)


class Metadata(Contract):
    name: Name


class ReleaseMetadata(Metadata):
    release: Name


class Reference(Contract):
    uri: Uri | Path
    digest: Digest


class Repository(Contract):
    uri: Uri
    inventory_digest: Digest
    prepared_inventory_digest: Digest
    materializer_image: Image | None = None

    @model_validator(mode="after")
    def materializer_required(self) -> Self:
        if self.inventory_digest != self.prepared_inventory_digest and self.materializer_image is None:
            raise ValueError("Different prepared inventory requires a pinned materializer")
        return self


class StartupModel(Contract):
    name: Annotated[str, Field(max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
    versions: tuple[Annotated[str, Field(max_length=19, pattern=r"^[1-9][0-9]*$")], ...] = Field(
        min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def unique_versions(self) -> Self:
        if len(set(self.versions)) != len(self.versions):
            raise ValueError("Duplicate model version")
        return self


class Quantities(Contract):
    cpu: Cpu
    memory: Memory


def cpu_value(value: str) -> Decimal:
    return Decimal(value[:-1]) / 1000 if value.endswith("m") else Decimal(value)


def memory_value(value: str) -> int:
    match = re.fullmatch(r"([0-9]+)(Ki|Mi|Gi|Ti|k|M|G|T)?", value)
    if match is None:
        raise ValueError("Invalid memory quantity")
    suffix = match[2] or ""
    multipliers = {
        "": 1,
        "k": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
    }
    return int(match[1]) * multipliers[suffix]


class Resources(Contract):
    requests: Quantities
    limits: Quantities
    gpu_count: Count

    @model_validator(mode="after")
    def valid_quantities(self) -> Self:
        if not 0 < cpu_value(self.requests.cpu) <= cpu_value(self.limits.cpu):
            raise ValueError("CPU requests must be positive and no greater than limits")
        if memory_value(self.requests.memory) > memory_value(self.limits.memory):
            raise ValueError("Memory requests exceed limits")
        return self


class ArtifactSubjects(Contract):
    image: Digest
    inventory_digest: Digest
    prepared_inventory_digest: Digest
    materializer_image: Digest | None = None
    source_code: Digest | None = None
    validation_input: Digest | None = None


class Evidence(Reference):
    subjects: ArtifactSubjects


class ReleaseSpec(Contract):
    image: Image
    repository: Repository
    models: tuple[StartupModel, ...] = Field(min_length=1, max_length=100)
    resources_per_replica: Resources
    lineage: ReleaseLineageV1
    lineage_manifest: Reference
    validation: Evidence
    security: Evidence | None = None

    @model_validator(mode="after")
    def consistent_subjects(self) -> Self:
        if len({model.name for model in self.models}) != len(self.models):
            raise ValueError("Duplicate startup model")
        if self.lineage.runtime_image != self.image:
            raise ValueError("Lineage runtime differs from release image")
        if self.lineage.origin == self.lineage.replaces_release:
            raise ValueError("Release cannot replace itself")
        repository = self.repository
        for evidence in (self.validation, self.security):
            if evidence is None:
                continue
            subjects = evidence.subjects
            materializer = repository.materializer_image
            if (
                subjects.image != self.image.split("@", 1)[1]
                or subjects.inventory_digest != repository.inventory_digest
                or subjects.prepared_inventory_digest != repository.prepared_inventory_digest
                or subjects.materializer_image != (materializer.split("@", 1)[1] if materializer else None)
            ):
                raise ValueError("Evidence subjects differ from release artifacts")
        return self


class ServingRelease(Contract):
    api_version: Literal["serving.triton-control/v1alpha1"]
    kind: Literal["ServingRelease"]
    metadata: ReleaseMetadata
    spec: ReleaseSpec


class Credentials(Contract):
    model_storage: Name
    image_registry: Name


class Rollout(Contract):
    max_surge: Count
    max_unavailable: Count


class TargetSpec(Contract):
    release: Reference
    replicas: PositiveCount
    capacity_owner: Literal["source"] = "source"
    credentials: Credentials
    rollout: Rollout

    @model_validator(mode="after")
    def valid_rollout(self) -> Self:
        if self.rollout.max_surge + self.rollout.max_unavailable == 0:
            raise ValueError("Rollout must allow progress")
        if self.rollout.max_unavailable > self.replicas:
            raise ValueError("Unavailable replicas exceed target replicas")
        return self


class ServingTarget(Contract):
    api_version: Literal["serving.triton-control/v1alpha1"]
    kind: Literal["ServingTarget"]
    metadata: Metadata
    spec: TargetSpec


class GitLocation(Contract):
    type: Literal["git"]
    repository: Uri
    ref: Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9/_.-]*$")]
    path: Path

    @model_validator(mode="after")
    def git_location(self) -> Self:
        if not self.repository.startswith("https://"):
            raise ValueError("Git repositories require HTTPS")
        if ".." in self.ref or "//" in self.ref or self.ref.endswith(("/", ".", ".lock")):
            raise ValueError("Invalid Git ref")
        return self


class S3Location(Contract):
    type: Literal["s3"]
    bucket: Annotated[str, Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")]
    key: Path


class ApprovalPolicy(Contract):
    mode: Literal["manual", "independently-approved"] = "manual"


class SourceSpec(Contract):
    location: Annotated[GitLocation | S3Location, Field(discriminator="type")]
    read_credential: Name
    allowed_targets: tuple[Name, ...] = Field(min_length=1, max_length=1000)
    interval_seconds: Annotated[int, Field(strict=True, ge=1, le=2_592_000)] = 86400
    approval: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    enabled: StrictBool = False

    @model_validator(mode="after")
    def unique_targets(self) -> Self:
        if len(set(self.allowed_targets)) != len(self.allowed_targets):
            raise ValueError("Duplicate target")
        return self


class MetadataSource(Contract):
    api_version: Literal["serving.triton-control/v1alpha1"]
    kind: Literal["MetadataSource"]
    metadata: Metadata
    spec: SourceSpec


Document = ServingRelease | ServingTarget | MetadataSource
DOCUMENT_MODELS: dict[str, type[ServingRelease] | type[ServingTarget] | type[MetadataSource]] = {
    "ServingRelease": ServingRelease,
    "ServingTarget": ServingTarget,
    "MetadataSource": MetadataSource,
}
