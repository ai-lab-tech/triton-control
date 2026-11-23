"""Pure, versioned immutable repository materialization contracts."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, model_validator
from typing_extensions import Self

from app.schemas.lineage import Contract, Digest, InventoryV1
from app.schemas.serving_metadata import Image, Name, Path, Reference

NORMALIZATION_VERSION = "triton-layout/v1"
FIXTURE_REVISION = "materializer-qualification/v3"


class Rewrite(Contract):
    """Explicit quoted string substitution; never execute config/model code."""

    file: Path
    relative: Path
    target: Path
    occurrences: Annotated[int, Field(strict=True, ge=1, le=100)] = 1

    @model_validator(mode="after")
    def configuration_only(self) -> Self:
        if self.file.split("/")[-1] not in {"config.pbtxt", "model.json"}:
            raise ValueError("Only explicit config.pbtxt/model.json path rewriting is supported")
        return self


class Normalization(Contract):
    version: Literal["identity/v1", "triton-layout/v1"] = "identity/v1"
    wrap_model: Name | None = None
    rewrites: tuple[Rewrite, ...] = Field(default=(), max_length=1000)

    @model_validator(mode="after")
    def supported(self) -> Self:
        if self.version == "identity/v1" and (self.wrap_model is not None or self.rewrites):
            raise ValueError("Identity normalization must not modify paths or bytes")
        keys = [(rule.file, rule.relative) for rule in self.rewrites]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate rewrite")
        return self


class ObjectVersion(Contract):
    path: Path
    version_id: Annotated[str, Field(min_length=1, max_length=1024, pattern=r"^[^\x00-\x1f]+$")]

    @model_validator(mode="after")
    def immutable(self) -> Self:
        if self.version_id == "null":
            raise ValueError("An immutable S3 version is required")
        return self


class MaterializationPlan(Contract):
    schema_version: Literal[1] = 1
    materializer_image: Image
    source_inventory: InventoryV1
    prepared_inventory: InventoryV1
    objects: tuple[ObjectVersion, ...] = Field(min_length=1, max_length=10000)
    normalization: Normalization = Field(default_factory=Normalization)

    @model_validator(mode="after")
    def inventories(self) -> Self:
        paths = {entry.path for entry in self.source_inventory.files}
        if not paths or len(paths) > 10000 or len(self.prepared_inventory.files) > 10000:
            raise ValueError("Inventory must be nonempty and bounded")
        if {entry.path for entry in self.objects} != paths or len(self.objects) != len(paths):
            raise ValueError("Every source file requires exactly one version")
        for inventory in (self.source_inventory, self.prepared_inventory):
            all_paths = {entry.path for entry in inventory.files}
            for path in all_paths:
                # This contract intentionally uses the same bounded ASCII path
                # subset as release metadata; neutral importer paths may be broader.
                from pydantic import TypeAdapter

                TypeAdapter(Path).validate_python(path)
                if len(path.split("/")) > 32 or any(
                    "/".join(path.split("/")[:i]) in all_paths for i in range(1, len(path.split("/")))
                ):
                    raise ValueError("Unsafe or conflicting file/directory paths")
        destinations = {
            ((self.normalization.wrap_model + "/") if self.normalization.wrap_model else "") + p for p in paths
        }
        if destinations != {entry.path for entry in self.prepared_inventory.files}:
            raise ValueError("Prepared paths must match the declared wrapping transform exactly")
        if self.normalization.wrap_model and "config.pbtxt" not in paths:
            raise ValueError("Wrapping requires an explicit single-model root config.pbtxt")
        for rule in self.normalization.rewrites:
            if rule.file not in paths or not any(
                p == rule.target or p.startswith(rule.target + "/") for p in destinations
            ):
                raise ValueError("Rewrite must reference inventoried configuration and local prepared artifacts")
        return self


class MaterializationReceipt(Contract):
    schema_version: Literal[1] = 1
    plan_digest: Digest
    source_inventory_digest: Digest
    prepared_inventory_digest: Digest
    normalization_digest: Digest
    materializer_image: Image


class Qualification(Contract):
    image: Image
    platform: Literal["linux/amd64", "linux/arm64"]
    fixture_revision: Literal["materializer-qualification/v3"]
    checks: dict[str, StrictBool]
    evidence: Reference


class MaterializerPolicy(Contract):
    """Administrator-provisioned policy; no default digest or self-approval."""

    image: Image
    platform: Literal["linux/amd64", "linux/arm64"]
    qualification: Qualification


class StorageBinding(Contract):
    """Trusted environment-local binding, resolved by the caller after authorization."""

    bucket: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")]
    prefix: Path | Literal[""]
    secret_name: Name
    endpoint: str | None = None
    region: Annotated[str, Field(min_length=1, max_length=128)] = "us-east-1"
    allow_http: StrictBool = False
    ca_secret_name: Name | None = None

    @model_validator(mode="after")
    def endpoint_url(self) -> Self:
        if self.endpoint is not None:
            from urllib.parse import urlsplit

            value = urlsplit(self.endpoint)
            if (
                value.scheme not in ({"https", "http"} if self.allow_http else {"https"})
                or not value.hostname
                or value.username
                or value.password
                or value.query
                or value.fragment
                or value.path not in {"", "/"}
            ):
                raise ValueError("Expected an administrator-configured S3 endpoint without credentials or paths")
        return self
