"""Standalone init-container program: no application imports or credential files."""

import hashlib
import json
import os
import shutil
import time
from contextlib import suppress
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, ContextManager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

UNVERSIONED_INPUT_CHANGED = 42
UNVERSIONED_INPUT_MESSAGE = "Unversioned task input"


class UnversionedInputChanged(Exception):
    pass


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
        try:
            return urlopen(request, timeout=document.get("download_timeout_seconds", 3600) + 60)  # nosec B310 -- server-configured internal URL
        except HTTPError as error:
            entry = document["files"][index]
            if error.code == 409 and "version_id" in entry and entry["version_id"] is None:
                raise UnversionedInputChanged(entry["path"]) from None
            raise

    try:
        stage(Path("/inputs"), document, fetch)
    except UnversionedInputChanged as error:
        message = f"{UNVERSIONED_INPUT_MESSAGE} {str(error)!r} changed or disappeared. Submit a new handoff revision."
        print(message, flush=True)
        with suppress(OSError):
            Path("/dev/termination-log").write_text(message, encoding="utf-8")
        raise SystemExit(UNVERSIONED_INPUT_CHANGED) from None
    except Exception:
        # Never echo a response body, credential or source URL into pod logs.
        print("Task input staging failed. Check source access, hashes and Python runtime; retry.", flush=True)
        raise SystemExit(1) from None
    print("Task inputs verified; editor may start.", flush=True)


if __name__ == "__main__":
    main()
