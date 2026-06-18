"""Protobuf and configuration utilities for Triton model configs.

Handles the version-aware loading and validation of Triton ``config.pbtxt``
files by dynamically selecting the correct protobuf descriptor for the
running Triton release:
  ``extract_triton_version(metadata)``      — parse the server version string
                                               from Triton's metadata response.
  ``get_model_config_class(version)``       — load the generated protobuf
                                               ``ModelConfig`` class that
                                               matches the Triton release branch;
                                               result is cached per version.
  ``validate_triton_config_pbtxt(content,
                                  version)``— parse and validate a ``config.pbtxt``
                                               string; raises
                                               ``UnprocessableEntityError`` on
                                               invalid protobuf text.
  ``extract_triton_error_detail(body)``     — extract the human-readable message
                                               from a Triton HTTP error response.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from google.protobuf import (
    descriptor_pb2,
    descriptor_pool,
    message_factory,
    text_format,
)
from grpc_tools import protoc  # type: ignore[import-untyped]

from app.exceptions import InternalError, UnprocessableEntityError, UnsupportedMediaTypeError
from protobuff.triton_release_map import TRITON_SEMVER_TO_RELEASE_BRANCH

PROTOBUFF_DIR = Path(__file__).resolve().parents[3] / "protobuff"
_MODEL_CONFIG_CLASS_CACHE: dict[str, type[Any]] = {}


def extract_triton_version(server_metadata: dict[str, Any] | None) -> str | None:
    """Return the Triton version string from server metadata, or None."""
    if not isinstance(server_metadata, dict):
        return None
    version = server_metadata.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def _map_triton_version_to_release_branch(triton_version: str | None) -> str | None:
    if not triton_version:
        return None

    value = triton_version.strip().lower()
    if not value:
        return None

    # Any value containing YY.MM maps to rYY.MM, if that branch exists locally.
    match = re.search(r"(\d{2}\.\d{2})", value)
    if match:
        return f"r{match.group(1)}"

    # Triton server metadata often reports semver (for example 2.55.0)
    # while schema folders are named by release branch (for example r25.02).
    semver_match = re.search(r"\b(2\.\d{2}\.\d+)\b", value)
    if not semver_match:
        return None

    semver = semver_match.group(1)
    return TRITON_SEMVER_TO_RELEASE_BRANCH.get(semver)


def _load_model_config_class(release_branch: str) -> type[Any]:
    if release_branch in _MODEL_CONFIG_CLASS_CACHE:
        return _MODEL_CONFIG_CLASS_CACHE[release_branch]

    proto_path = PROTOBUFF_DIR / release_branch / "model_config.proto"
    if not proto_path.exists():
        raise UnprocessableEntityError(
            f"Missing model_config.proto for release branch {release_branch}: {proto_path}",
        )

    with tempfile.TemporaryDirectory(prefix="model_config_desc_") as tmp_dir:
        descriptor_path = Path(tmp_dir) / "model_config.desc"
        rc = protoc.main(
            [
                "grpc_tools.protoc",
                f"-I{proto_path.parent}",
                f"--descriptor_set_out={descriptor_path}",
                "--include_imports",
                str(proto_path),
            ]
        )
        if rc != 0 or not descriptor_path.exists():
            raise InternalError(f"Failed generating descriptor for {proto_path}")

        file_set = descriptor_pb2.FileDescriptorSet()
        file_set.ParseFromString(descriptor_path.read_bytes())

    pool = descriptor_pool.DescriptorPool()
    for file_descriptor in file_set.file:
        pool.Add(file_descriptor)

    try:
        model_descriptor = pool.FindMessageTypeByName("inference.ModelConfig")
    except Exception as exc:
        raise InternalError(
            f"ModelConfig message not found in {proto_path}",
        ) from exc

    model_class = message_factory.GetMessageClass(model_descriptor)
    _MODEL_CONFIG_CLASS_CACHE[release_branch] = model_class
    return model_class


def validate_triton_config_pbtxt(content: bytes, triton_version: str | None) -> None:
    """Parse *content* as a Triton model config.pbtxt and raise HTTP 422 on error."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedMediaTypeError("config.pbtxt must be valid UTF-8 text") from exc

    release_branch = _map_triton_version_to_release_branch(triton_version)
    if not release_branch:
        raise UnprocessableEntityError(
            "Unable to map Triton version to release branch. "
            f"Detected version: {triton_version or 'unknown'}"
        )

    model_config_class = _load_model_config_class(release_branch)
    model_config = model_config_class()
    try:
        text_format.Parse(text, model_config)
    except text_format.ParseError as exc:
        hint_parts: list[str] = []

        if text.count("[") > text.count("]"):
            hint_parts.append("Likely missing closing square bracket ']'.")
        elif text.count("[") < text.count("]"):
            hint_parts.append("Likely extra closing square bracket ']'.")

        match = re.search(r"(\d+):(\d+)", str(exc))
        if match:
            line_number = int(match.group(1))
            lines = text.splitlines()
            if 1 <= line_number <= len(lines):
                hint_parts.append(f"Line {line_number}: {lines[line_number - 1].strip()}")

        hint = f" Hint: {' '.join(hint_parts)}" if hint_parts else ""
        raise UnprocessableEntityError(
            f"Invalid Triton config.pbtxt ({release_branch}): {exc}{hint}",
        ) from exc


def extract_triton_error_detail(exc: Exception) -> str:
    """Extract a human-readable error message from a failed Triton HTTP response."""
    response = getattr(exc, "response", None)
    if response is None:
        return "Triton request failed"

    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

    text = getattr(response, "text", "")
    if isinstance(text, str) and text.strip():
        return text.strip()

    status_code = getattr(response, "status_code", None)
    return f"Triton request failed with HTTP {status_code}" if status_code else "Triton request failed"
