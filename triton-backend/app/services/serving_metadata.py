"""Offline serving metadata loading; no trust or approval checks."""

from app.schemas.serving_metadata import API_VERSION, DOCUMENT_MODELS, Document
from app.services.contract_io import (
    MAX_BYTES,
    MAX_DEPTH,
    MAX_NODES,
    Format,
    MetadataError,
    ValidatedContract,
    load_contract,
    parse_document,
)

# Preserve the existing public loader API while sharing the parser implementation.
__all__ = [
    "MAX_BYTES", "MAX_DEPTH", "MAX_NODES", "Format", "MetadataError",
    "ValidatedMetadata", "load_metadata", "parse_document",
]

ValidatedMetadata = ValidatedContract[Document]


def load_metadata(raw: bytes, *, format: Format = "auto") -> ValidatedMetadata:
    return load_contract(
        raw, format=format, api_version=API_VERSION, models=DOCUMENT_MODELS,
        schema_package="app.services.serving_contracts", semantic_path="$.spec",
    )
