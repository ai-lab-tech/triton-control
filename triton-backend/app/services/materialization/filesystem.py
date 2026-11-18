"""Bounded no-follow repository measurement; no model code is ever executed."""

import hashlib
import os
import stat
import time
from pathlib import Path

from app.schemas.lineage import FileEntry, InventoryV1
from app.services.candidates.artifacts import CaptureLimits, _open_directory, _signature, safe_path
from app.services.materialization.normalization import MaterializationError


def inventory(root: Path, limits: CaptureLimits) -> InventoryV1:
    started = time.monotonic()
    entries: list[FileEntry] = []
    total = nodes = 0

    def check() -> None:
        if time.monotonic() - started > limits.deadline_seconds:
            raise MaterializationError("Repository measurement timed out")

    def walk(fd: int, prefix: str) -> None:
        nonlocal total, nodes
        names = []
        with os.scandir(fd) as iterator:
            for entry in iterator:
                nodes += 1
                if nodes > limits.max_files * 3:
                    raise MaterializationError("Repository contains too many paths")
                names.append(entry.name)
        for name in sorted(names):
            check()
            relative = safe_path(prefix + name, limits)
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    if _signature(os.fstat(child)) != _signature(before):
                        raise MaterializationError("Repository directory changed")
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                if len(entries) >= limits.max_files or before.st_size > limits.max_file_bytes:
                    raise MaterializationError("Repository exceeds file limits")
                digest, measured = hashlib.sha256(), 0
                opened = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(opened, "rb") as stream:
                    if _signature(os.fstat(stream.fileno())) != _signature(before):
                        raise MaterializationError("Repository file changed")
                    while chunk := stream.read(1024**2):
                        check()
                        measured += len(chunk)
                        total += len(chunk)
                        if total > limits.max_bytes or measured > before.st_size:
                            raise MaterializationError("Repository exceeds byte limits")
                        digest.update(chunk)
                    if measured != before.st_size or _signature(os.fstat(stream.fileno())) != _signature(before):
                        raise MaterializationError("Repository file changed during measurement")
                entries.append(FileEntry(path=relative, size=measured, digest="sha256:" + digest.hexdigest()))
            else:
                raise MaterializationError("Repository contains a link or special file")
        if set(os.listdir(fd)) != set(names):
            raise MaterializationError("Repository directory changed during measurement")

    fd = _open_directory(root)
    try:
        walk(fd, "")
    finally:
        os.close(fd)
    return InventoryV1(files=tuple(entries))
