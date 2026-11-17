"""Standalone init-container program: no application imports or credential files."""

import hashlib
import json
import os
import shutil
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
        for index, entry in enumerate(document["files"]):
            relative = PurePosixPath(entry["path"])
            if relative.is_absolute() or any(p in {".", ".."} for p in relative.parts) or "\\" in entry["path"]:
                raise ValueError("Unsafe input path")
            destination = pending / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            measured, checksum = 0, hashlib.sha256()
            with fetch(index) as response, destination.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    measured += len(chunk)
                    total += len(chunk)
                    if measured > entry["size"] or total > 3 * 1024**3:
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
        return urlopen(request, timeout=180)  # nosec B310 -- server-configured internal URL

    try:
        stage(Path("/inputs"), document, fetch)
    except Exception:
        # Never echo a response body, credential or source URL into pod logs.
        print("Task input staging failed. Check source access, hashes and Python runtime; retry.", flush=True)
        raise SystemExit(1) from None
    print("Task inputs verified; editor may start.", flush=True)


if __name__ == "__main__":
    main()
