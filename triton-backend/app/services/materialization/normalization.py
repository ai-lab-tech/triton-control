"""Explicit v1 preparation, identical in development validation and release startup."""

import json
import shutil
from pathlib import Path

from app.schemas.lineage import InventoryV1
from app.schemas.materialization import Normalization


class MaterializationError(ValueError):
    """Safe, bounded diagnostic: never include object content or credentials."""


def normalize(source: Path, destination: Path, inventory: InventoryV1, spec: Normalization) -> None:
    prefix = spec.wrap_model + "/" if spec.wrap_model else ""
    for entry in sorted(inventory.files, key=lambda file: file.path):
        target = destination / (prefix + entry.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / entry.path, target)
    for rule in spec.rewrites:
        target = destination / (prefix + rule.file)
        if target.stat().st_size > 1024**2:
            raise MaterializationError("Configuration exceeds normalization size limit")
        # Exact quoted-token rewriting is deliberately explicit rather than the
        # legacy sync worker's heuristic sed substitutions. Formatting is preserved.
        content = target.read_bytes().decode("utf-8")
        old = json.dumps(rule.relative, ensure_ascii=False)
        new = json.dumps("/models/" + rule.target, ensure_ascii=False)
        if content.count(old) != rule.occurrences:
            raise MaterializationError("Declared path rewrite count does not match configuration")
        target.write_bytes(content.replace(old, new).encode("utf-8"))
