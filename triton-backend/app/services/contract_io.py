"""Bounded offline contract loading shared by serving and validation documents."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Generic, Iterator, Literal, Mapping, TypeVar

import yaml  # type: ignore[import-untyped]
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ValidationError
from referencing import Registry
from yaml.events import (  # type: ignore[import-untyped]
    AliasEvent,
    DocumentEndEvent,
    DocumentStartEvent,
    Event,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
    StreamEndEvent,
    StreamStartEvent,
)

from app.services.lineage import canonical_bytes, digest

MAX_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 10_000
Format = Literal["json", "yaml", "auto"]


class MetadataError(ValueError):
    """Safe diagnostic: never include document values, arbitrary keys or parser text."""

    def __init__(self, code: str, path: str = "$") -> None:
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


class _Parser:
    """Consume YAML events with bounds before constructing any container tree."""

    def __init__(self, events: Iterator[Event]) -> None:
        self.events = events
        self.nodes = 0

    def take(self) -> Event:
        return next(self.events)

    def require(self, event_type: type[Event]) -> None:
        if not isinstance(self.take(), event_type):
            raise MetadataError("single-document-required")

    def node(self, event: Event, depth: int = 1) -> Any:
        self.nodes += 1
        if depth > MAX_DEPTH or self.nodes > MAX_NODES:
            raise MetadataError("structure-limit")
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) or getattr(event, "tag", None):
            raise MetadataError("yaml-reference-or-tag")
        if isinstance(event, ScalarEvent):
            if event.style is not None:
                return event.value
            value = event.value
            if value == "null":
                return None
            if value in {"true", "false"}:
                return value == "true"
            if re.fullmatch(r"-?(0|[1-9][0-9]*)", value):
                if len(value) > 20:
                    raise MetadataError("number-limit")
                return int(value)
            # Contracts have no floating-point values. Do not coerce 1.0 to 1.
            if re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?", value):
                raise MetadataError("non-integer-number")
            return value
        if isinstance(event, SequenceStartEvent):
            result = []
            while not isinstance(item := self.take(), SequenceEndEvent):
                result.append(self.node(item, depth + 1))
            return result
        if isinstance(event, MappingStartEvent):
            mapping: dict[str, Any] = {}
            while not isinstance(item := self.take(), MappingEndEvent):
                key = self.node(item, depth + 1)
                if not isinstance(key, str) or key == "<<":
                    raise MetadataError("invalid-mapping-key")
                if key in mapping:
                    raise MetadataError("duplicate-key")
                mapping[key] = self.node(self.take(), depth + 1)
            return mapping
        raise MetadataError("invalid-document")


def parse_document(raw: bytes, format: Format) -> Any:
    if len(raw) > MAX_BYTES:
        raise MetadataError("byte-limit")
    try:
        text = raw.decode("utf-8")
        parser = _Parser(iter(yaml.parse(text)))
        parser.require(StreamStartEvent)
        parser.require(DocumentStartEvent)
        result = parser.node(parser.take())
        parser.require(DocumentEndEvent)
        parser.require(StreamEndEvent)
        is_json = format == "json" or (format == "auto" and text.lstrip().startswith(("{", "[")))
        if is_json:
            # The bounded event pass already checked duplicates/depth/counts.
            # Also enforce JSON grammar instead of accepting YAML in a .json file.
            result = parse_json_document(text)
        return result
    except MetadataError:
        raise
    except (UnicodeError, yaml.YAMLError, ValueError, RecursionError, StopIteration):
        raise MetadataError("invalid-document") from None


def parse_json_document(raw: bytes | str) -> Any:
    """Decode size-checked JSON while rejecting ambiguous object keys."""
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        return json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_reject_constant,
        )
    except MetadataError:
        raise
    except (ValueError, RecursionError):
        raise MetadataError("invalid-document") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MetadataError("duplicate-key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise MetadataError("non-integer-number")


DocumentT = TypeVar("DocumentT", bound=BaseModel)


@dataclass(frozen=True)
class ValidatedContract(Generic[DocumentT]):
    document: DocumentT
    canonical: bytes
    digest: str


@lru_cache(maxsize=6)
def _validator(package: str, kind: str) -> Draft202012Validator:
    schema = json.loads(files(package).joinpath(f"{kind}.schema.json").read_text())
    return Draft202012Validator(schema, registry=Registry())


def load_contract(
    raw: bytes, *, format: Format, api_version: str, models: Mapping[str, type[DocumentT]],
    schema_package: str, semantic_path: str = "$",
) -> ValidatedContract[DocumentT]:
    if format not in {"auto", "yaml", "json"}:
        raise MetadataError("unsupported-format")
    data = parse_document(raw, format)
    if not isinstance(data, dict):
        raise MetadataError("mapping-required")
    if data.get("apiVersion") != api_version:
        raise MetadataError("unsupported-version", "$.apiVersion")
    kind = data.get("kind")
    if not isinstance(kind, str) or kind not in models:
        raise MetadataError("unsupported-kind", "$.kind")
    if next(_validator(schema_package, kind).iter_errors(data), None) is not None:
        raise MetadataError("schema-invalid")
    try:
        document = models[kind].model_validate(data)
    except ValidationError:
        raise MetadataError("semantic-invalid", semantic_path) from None
    value = document.model_dump(mode="json", by_alias=True)
    return ValidatedContract(document, canonical_bytes(value), digest(value))
