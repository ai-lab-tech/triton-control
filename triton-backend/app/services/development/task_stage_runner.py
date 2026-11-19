"""Standalone init-container program: no application imports or credential files."""

import hashlib
import json
import os
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, ContextManager
from urllib.request import Request, urlopen


def stage(root: Path, document: dict[str, Any], fetch: Callable[[int], ContextManager[BinaryIO]]) -> None:
    """Publish the complete verified input set atomically; never extract archives."""
    pending, final = root / "pending", root / "verified"
    shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir()
    try:
        total = 0
        if any(not isinstance(entry.get("size"), int) or entry["size"] < 0 for entry in document["files"]):
            raise ValueError("Invalid declared file size")
        max_bytes = document.get("max_input_bytes", sum(entry["size"] for entry in document["files"]))
        for index, entry in enumerate(document["files"]):
            relative = PurePosixPath(entry["path"])
            if relative.is_absolute() or any(p in {".", ".."} for p in relative.parts) or "\\" in entry["path"]:
                raise ValueError("Unsafe input path")
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            measured, checksum = 0, hashlib.sha256()
            started = time.monotonic()
            with fetch(index) as response, destination.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    if time.monotonic() - started > document.get("download_timeout_seconds", 3600):
                        raise ValueError("Task input download time limit exceeded")
                    measured += len(chunk)
                    total += len(chunk)
                    if measured > entry["size"] or total > max_bytes:
                        raise ValueError("Input exceeds declared limits")
                    checksum.update(chunk)
                    output.write(chunk)
            if measured != entry["size"] or "sha256:" + checksum.hexdigest() != entry["digest"]:
                raise ValueError("Input hash mismatch")
            destination.chmod(0o444)
        (pending / "task.json").write_text(json.dumps(document, sort_keys=True))
        (pending / "task.json").chmod(0o444)
        # Only this init container mounts inputs writable; editor has a read-only mount.
        shutil.rmtree(final, ignore_errors=True)
        pending.rename(final)
    except BaseException:
        shutil.rmtree(pending, ignore_errors=True)
        raise


def main() -> None:
    document = json.loads(Path("/stager/task.json").read_text())
    base = os.environ["TASK_INPUT_URL"]
    token = os.environ["TASK_INPUT_TOKEN"]

    def fetch(index: int) -> Any:
        request = Request(base + "/" + str(index), headers={"Authorization": "Bearer " + token})
        return urlopen(request, timeout=document.get("download_timeout_seconds", 3600) + 60)  # nosec B310 -- server-configured internal URL

    try:
        stage(Path("/inputs"), document, fetch)
    except Exception:
        # Never echo a response body, credential or source URL into pod logs.
        print("Task input staging failed. Check source access, hashes and Python runtime; retry.", flush=True)
        raise SystemExit(1) from None
    print("Task inputs verified; editor may start.", flush=True)


if __name__ == "__main__":
    main()
