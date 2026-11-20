"""Derive source provenance exclusively from declared origins and staged files."""

import json
import re
from pathlib import Path
from typing import Literal

from app.schemas.experiment_snapshots import ExampleSource, HFSource, PreparationRequest, SourceProvenance
from app.schemas.lineage import FileEntry, InventoryV1
from app.services.lineage import digest, inventory_digest

WORKSPACE_PREFIX = ".workspace/"
MAX_MANIFEST_BYTES = 1024**2
STRING_OPTIONS = (
    "model_name", "runtime", "task", "image", "device",
)
INTEGER_OPTIONS = (
    "gpu_count", "tensor_parallel_size", "max_new_tokens", "max_model_len",
)
HINT = re.compile(r"[A-Za-z0-9_./:@+-]{1,256}")


def _hint(value: object) -> str | None:
    if isinstance(value, str) and HINT.fullmatch(value):
        return value
    return None


def _hf_hints(path: Path, entry: FileEntry) -> tuple[str | None, str | None, dict[str, str | int]]:
    if entry.size > MAX_MANIFEST_BYTES or path.name != "serving.json":
        return None, None, {}
    try:
        document = json.loads(path.read_bytes())
    except (ValueError, RecursionError):
        return None, None, {}
    if not isinstance(document, dict):
        return None, None, {}
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

    model_files = {entry.path: entry for entry in inventory.files}
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
    if source.workspace_model_root and model_files:
        prefix = WORKSPACE_PREFIX + source.workspace_model_root + "/"
        models = {model.name: set(model.versions) for model in request.models}
        for path, entry in workspace_files.items():
            if not path.startswith(prefix):
                continue
            relative = path.removeprefix(prefix)
            model, _, file = relative.partition("/")
            model_version = file.split("/", 1)[0]
            if model in models and file and (not model_version.isdigit() or model_version in models[model]):
                deployed = model_files.get(relative)
                if deployed is None or (entry.size, entry.digest) != (deployed.size, deployed.digest):
                    gaps.add("workspace_model_bytes_differ")
                    break

    if isinstance(source, HFSource):
        manifest = workspace_files.get(WORKSPACE_PREFIX + source.serving_manifest) if source.serving_manifest else None
        generator, version, options = _hf_hints(staging / manifest.path, manifest) if manifest else (None, None, {})
        if not generator or not version:
            gaps.add("hf_generator_metadata_missing")
        captured = {**model_files, **workspace_files}
        checkpoint = None
        if source.checkpoint_files and all(path in captured for path in source.checkpoint_files):
            checkpoint = inventory_digest(InventoryV1(
                files=tuple(captured[path] for path in sorted(set(source.checkpoint_files))),
            ))
        else:
            gaps.add("hf_checkpoint_fingerprint_missing")
        if not source.repository or not source.resolved_commit:
            gaps.add("hf_source_revision_unverified")
        revision_fingerprint = None
        if source.repository and (source.revision or source.resolved_commit) and checkpoint:
            revision_fingerprint = digest({
                "repository": source.repository, "revision": source.revision,
                "resolved_commit": source.resolved_commit, "checkpoint_inventory": checkpoint,
            })
        return "hf", SourceProvenance(
            submitter_user_id=submitter_user_id,
            documents=documents,
            serving_manifest=manifest,
            generator=generator,
            generator_version=version,
            generator_options=options,
            checkpoint_inventory=checkpoint,
            revision_fingerprint=revision_fingerprint,
        ), gaps

    provenance = SourceProvenance(submitter_user_id=submitter_user_id, documents=documents)
    if isinstance(source, ExampleSource):
        if not source.repository or not source.commit or not source.project_path:
            gaps.add("example_origin_incomplete")
        return "git", provenance, gaps
    return "manual", provenance, gaps
