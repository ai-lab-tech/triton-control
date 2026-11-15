"""Measure a frozen model directory. Provenance metadata lives outside this root."""

import hashlib
import stat
from pathlib import Path

from app.schemas.lineage import FileEntry, InventoryV1


def measure(root: Path) -> InventoryV1:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Expected a frozen directory")
    entries = []
    for path in sorted(root.rglob("*")):
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ValueError("Links and special files are unsupported")
        data = path.read_bytes()
        after = path.lstat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Input changed during inventory")
        entries.append(
            FileEntry(
                path=path.relative_to(root).as_posix(),
                size=len(data),
                digest="sha256:" + hashlib.sha256(data).hexdigest(),
            )
        )
    return InventoryV1(files=tuple(entries))
