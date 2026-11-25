"""Portable publication receipt. Referenced files are relative to the target."""

from typing import Literal

from app.schemas.lineage import Contract, Digest
from app.schemas.serving_metadata import Path


class PublicationReceipt(Contract):
    schema_version: Literal[1]
    origin_release: Path
    release_digest: Digest
    candidate: Path
    inventories: dict[Digest, Path]
    origin_plan: Path
    published_plan: Path
    validation_archive: Path
    target_digest: Digest
