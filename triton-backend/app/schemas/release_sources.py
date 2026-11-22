"""API representation of a registration backed by the shared serving contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, PositiveInt

from app.schemas.serving_metadata import MetadataSource

Provenance = Literal["database", "environment", "file"]


class ReleaseSource(BaseModel):
    source: MetadataSource
    provenance: Provenance
    revision: PositiveInt
    editable: bool


class ReleaseSourceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: MetadataSource
    expected_revision: PositiveInt
