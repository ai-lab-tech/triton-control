"""Standalone read-only inventory command for retired task volumes."""

import hashlib
import json
import os
import stat
from pathlib import Path


def inventory(root: Path) -> dict[str, str]:
    result = {}

    def walk(directory: Path) -> None:
        for entry in directory.iterdir():
            mode = entry.lstat().st_mode
            if stat.S_ISDIR(mode):
                walk(entry)
            elif stat.S_ISREG(mode):
                with entry.open("rb") as stream:
                    result[entry.relative_to(root).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
                if len(result) > 10000:
                    raise ValueError("Volume exceeds the inspection limit")
            else:
                raise ValueError("Volume contains a link or special file")

    walk(root)
    return result


if __name__ == "__main__":
    print(json.dumps(inventory(Path(os.environ.get("CLAIM_ROOT", "/inspection")))))
