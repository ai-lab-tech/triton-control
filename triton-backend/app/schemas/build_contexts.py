"""Versioned, credential-free build-context contracts; no I/O."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from app.schemas.lineage import Digest, FileEntry
from app.schemas.operations import OperationResponse

Path = Annotated[str, Field(min_length=1, max_length=1024)]
Image = Annotated[str, Field(max_length=512, pattern=r"^[a-z0-9][a-z0-9.:/_-]*@sha256:[0-9a-f]{64}$")]


class BuildContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    task_id: UUID | None = None
    dockerfile_only: Literal[True] | None = None
    staging_profile: str | None = Field(default=None, min_length=1, max_length=128)
    archive_digest: Digest
    archive_bytes: Annotated[int, Field(strict=True, ge=1024, le=1073741824)]
    dockerfile: Path = "Dockerfile"
    allowed_paths: tuple[Path, ...] = Field(min_length=1, max_length=100)
    dependency_files: tuple[Path, ...] = Field(default=(), max_length=100)
    base_images: tuple[Image, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def safe_paths(self) -> Self:
        for path in (self.dockerfile, *self.dependency_files, *(p for p in self.allowed_paths if p != ".")):
            FileEntry(path=path, size=0, digest=self.archive_digest)
            if len(path.encode("utf-8")) > 1024 or len(path.split("/")) > 32:
                raise ValueError("Build path exceeds limits")
        if self.dockerfile_only and (self.staging_profile or self.allowed_paths != (self.dockerfile,)
                                    or self.dependency_files or self.archive_bytes > 131072):
            raise ValueError("A Dockerfile build accepts only the selected Dockerfile")
        if len(set(self.base_images)) != len(self.base_images):
            raise ValueError("Duplicate base image")
        return self


class ContextFile(FileEntry):
    executable: bool


class ContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    files: tuple[ContextFile, ...]
    inventory_digest: Digest
    dockerfile_digest: Digest
    dependencies: dict[str, str]
    base_images: tuple[str, ...]
    archive_digest: Digest
    archive_bytes: int


class BuildContextResponse(BaseModel):
    id: str
    operation: OperationResponse
    upload_url: str
    expires_at: datetime
    ready: bool
    manifest: ContextManifest | None = None
