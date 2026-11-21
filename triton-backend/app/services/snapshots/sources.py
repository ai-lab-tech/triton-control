"""Derive source provenance exclusively from declared origins and staged files."""

import json
import re
from pathlib import Path
from typing import Any, Literal

from app.schemas.experiment_snapshots import (
    ExampleSource,
    HFSource,
    PreparationRequest,
    SourceProvenance,
    selected_model_path,
)
from app.schemas.lineage import FileEntry, InventoryV1
from app.services.lineage import digest, inventory_digest

WORKSPACE_PREFIX = ".workspace/"
MAX_MANIFEST_BYTES = 1024**2
STRING_OPTIONS = (
    "model_name", "runtime", "task", "image", "device", "triton_version", "requirements_file",
)
INTEGER_OPTIONS = (
    "gpu_count", "tensor_parallel_size", "max_new_tokens", "max_model_len",
)
HINT = re.compile(r"[A-Za-z0-9_./:@+-]{1,256}")


def _hint(value: object) -> str | None:
    if isinstance(value, str) and HINT.fullmatch(value):
        return value
    return None


def _hf_document(staging: Path, entry: FileEntry | None) -> dict[str, Any]:
    if entry is None or entry.size > MAX_MANIFEST_BYTES:
        return {}
    try:
        document = json.loads((staging / entry.path).read_bytes())
    except (ValueError, RecursionError):
        return {}
    return document if isinstance(document, dict) else {}


def _hf_hints(document: dict[str, Any]) -> tuple[str | None, str | None, dict[str, str | int]]:
    options: dict[str, str | int] = {}
    for key in STRING_OPTIONS:
        value = _hint(document.get(key))
        if value is not None:
            options[key] = value
    for key in INTEGER_OPTIONS:
        number = document.get(key)
        if type(number) is int and 0 <= number <= 2**31 - 1:
            options[key] = number
    return _hint(document.get("generator")), _hint(document.get("generator_version")), options


def _hf_origin_conflicts(source: HFSource, document: dict[str, Any]) -> bool:
    repository, revision = document.get("repo_id"), document.get("revision")
    if isinstance(repository, str) and source.repository and repository != source.repository:
        return True
    if not isinstance(revision, str) or not revision:
        return False
    # A symbolic ref and its resolved commit are different representations, not
    # evidence of a conflict. Compare commits with commits and refs with refs.
    is_commit = bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", revision))
    for declared in (source.revision, source.resolved_commit):
        if declared and bool(re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", declared)) == is_commit:
            if revision != declared:
                return True
    return False


def workspace_model_files(request: PreparationRequest, workspace: InventoryV1) -> dict[str, FileEntry]:
    root = request.source.workspace_model_root if request.source else None
    if root is None:
        return {}
    prefix = WORKSPACE_PREFIX if root == "." else f"{WORKSPACE_PREFIX}{root}/"
    models = {model.name: set(model.versions) for model in request.models}
    files = {}
    for entry in workspace.files:
        if entry.path.startswith(prefix):
            path = entry.path.removeprefix(prefix)
            if "/" in path and selected_model_path(path, models):
                files[path] = entry
    return files


def _hf_provenance(
    source: HFSource, staging: Path, files: dict[str, FileEntry], provenance: SourceProvenance, gaps: set[str],
) -> SourceProvenance:
    manifest = files.get(WORKSPACE_PREFIX + source.serving_manifest) if source.serving_manifest else None
    generator, version, options = _hf_hints(_hf_document(staging, manifest))
    if source.acquisition_record:
        record = files[WORKSPACE_PREFIX + source.acquisition_record]
        document = _hf_document(staging, record)
        record_generator, record_version, acquired_options = _hf_hints(document)
        serving_image = _hint(document.get("serving_image"))
        if serving_image is not None:
            if "image" in acquired_options and acquired_options["image"] != serving_image:
                gaps.add("hf_generator_metadata_conflict")
            acquired_options["image"] = serving_image
        if _hf_origin_conflicts(source, document):
            gaps.add("hf_source_revision_conflict")
        if not acquired_options:
            gaps.add("hf_acquisition_options_missing")
        if (
            (record_generator and generator and generator != record_generator)
            or (record_version and version and version != record_version)
            or any(options[key] != acquired_options[key] for key in options.keys() & acquired_options.keys())
        ):
            gaps.add("hf_generator_metadata_conflict")
        generator = generator or record_generator
        version = version or record_version
        options = {**acquired_options, **options}
    if not generator or not version:
        gaps.add("hf_generator_metadata_missing")
    checkpoint = None
    if source.checkpoint_files and all(path in files for path in source.checkpoint_files):
        checkpoint = inventory_digest(InventoryV1(
            files=tuple(files[path] for path in sorted(set(source.checkpoint_files))),
        ))
    else:
        gaps.add("hf_checkpoint_fingerprint_missing")
    if not source.repository or not source.resolved_commit:
        gaps.add("hf_source_revision_missing")
    revision_fingerprint = None
    if (source.repository and (source.revision or source.resolved_commit) and checkpoint
            and "hf_source_revision_conflict" not in gaps):
        revision_fingerprint = digest({
            "repository": source.repository,
            "revision": source.revision,
            "resolved_commit": source.resolved_commit,
            "checkpoint_inventory": checkpoint,
        })
    return SourceProvenance(
        submitter_user_id=provenance.submitter_user_id,
        documents=provenance.documents,
        serving_manifest=manifest,
        generator=generator,
        generator_version=version,
        generator_options=options,
        checkpoint_inventory=checkpoint,
        revision_fingerprint=revision_fingerprint,
    )


def capture_provenance(
    request: PreparationRequest,
    submitter_user_id: int,
    staging: Path,
    inventory: InventoryV1,
    workspace: InventoryV1,
) -> tuple[Literal["manual", "git", "hf"], SourceProvenance | None, set[str]]:
    source = request.source
    if source is None:
        return "manual", None, set()

    workspace_files = {entry.path: entry for entry in workspace.files}
    paths = dict.fromkeys(
        path for path in (source.model_card, source.license_file, *source.source_documents) if path is not None
    )
    documents = tuple(workspace_files[WORKSPACE_PREFIX + path] for path in paths)
    gaps = set()
    if source.model_card is None:
        gaps.add("source_model_card_missing")
    if source.license_file is None:
        gaps.add("source_license_missing")
    provenance = SourceProvenance(submitter_user_id=submitter_user_id, documents=documents)
    if isinstance(source, HFSource):
        captured = {entry.path: entry for entry in (*inventory.files, *workspace.files)}
        return "hf", _hf_provenance(source, staging, captured, provenance, gaps), gaps
    if isinstance(source, ExampleSource):
        if not source.repository or not source.commit or not source.project_path:
            gaps.add("example_origin_incomplete")
        return "git", provenance, gaps
    return "manual", provenance, gaps
