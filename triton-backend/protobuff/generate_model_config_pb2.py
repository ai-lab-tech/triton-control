#!/usr/bin/env python3
"""Generate Python protobuf modules for all local release branches.

Input:
  protobuff/rYY.MM/model_config.proto

Output:
  protobuff/generated/rYY_MM/model_config_pb2.py
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from grpc_tools import protoc

ROOT = Path(__file__).resolve().parent
OUT_ROOT = ROOT / "generated"


def _release_dirs() -> list[Path]:
    if not ROOT.exists():
        return []
    return sorted(
        d
        for d in ROOT.iterdir()
        if d.is_dir() and re.fullmatch(r"r\d{2}\.\d{2}", d.name)
    )


def _sanitize(name: str) -> str:
    return name.replace(".", "_").replace("-", "_")


def _write_init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("", encoding="utf-8")


def main() -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_init(OUT_ROOT / "__init__.py")

    generated = 0
    skipped = 0
    generated_releases: list[tuple[str, str]] = []

    for release_dir in _release_dirs():
        proto = release_dir / "model_config.proto"
        if not proto.exists():
            print(f"[skip] {release_dir.name}: missing model_config.proto")
            skipped += 1
            continue

        target_pkg = OUT_ROOT / _sanitize(release_dir.name)
        target_pkg.mkdir(parents=True, exist_ok=True)
        _write_init(target_pkg / "__init__.py")

        rc = protoc.main(
            [
                "grpc_tools.protoc",
                f"-I{release_dir}",
                f"--python_out={target_pkg}",
                str(proto),
            ]
        )
        if rc != 0:
            print(f"[fail] {release_dir.name}: protoc failed")
            return 1

        generated_file = target_pkg / "model_config_pb2.py"
        if not generated_file.exists():
            print(f"[fail] {release_dir.name}: model_config_pb2.py not generated")
            return 1

        print(f"[ok]   {release_dir.name} -> {generated_file}")
        generated += 1
        generated_releases.append((release_dir.name, _sanitize(release_dir.name)))

    # Remove stale generated folders for releases that no longer exist.
    current = {_sanitize(d.name) for d in _release_dirs()}
    for child in OUT_ROOT.iterdir():
        if not child.is_dir():
            continue
        if child.name in {"__pycache__"}:
            continue
        if child.name not in current:
            shutil.rmtree(child)
            print(f"[clean] removed stale generated package: {child.name}")

    # Build static registry consumed by API code.
    registry_lines = [
        '"""Auto-generated registry for release-specific ModelConfig classes."""',
        "",
    ]

    for release_name, safe_name in sorted(generated_releases):
        alias = f"ModelConfig_{safe_name}"
        registry_lines.append(
            f"from protobuff.generated.{safe_name}.model_config_pb2 import ModelConfig as {alias}"
        )

    registry_lines.append("")
    registry_lines.append("MODEL_CONFIG_CLASSES = {")
    for release_name, safe_name in sorted(generated_releases):
        alias = f"ModelConfig_{safe_name}"
        registry_lines.append(f'    "{release_name}": {alias},')
    registry_lines.append("}")
    registry_lines.append("")

    (OUT_ROOT / "registry.py").write_text("\n".join(registry_lines), encoding="utf-8")
    print("[ok]   updated generated registry")

    print(f"[done] generated={generated}, skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
