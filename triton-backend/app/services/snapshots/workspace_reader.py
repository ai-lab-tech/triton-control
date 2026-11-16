"""Fixed, isolated-Python reader executed in an authorized workspace; no writes."""

import base64
import hashlib
import json
import os
import stat
import sys
import time


def read(root: str, paths: tuple[str, ...], max_bytes: int, max_file_bytes: int) -> list[dict[str, str]]:
    started = time.monotonic()
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    total = 0
    result = []

    def one(path: str) -> tuple[bytes, tuple[int, ...]]:
        fd = os.dup(root_fd)
        try:
            parts = path.split("/")
            if any(part in {"", ".", ".."} for part in parts) or "\\" in path:
                raise ValueError("unsafe")
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(child, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_file_bytes:
                    raise ValueError("unsafe")
                data = stream.read(max_file_bytes + 1)
                after = os.fstat(stream.fileno())
                def stable(s: os.stat_result) -> tuple[int, ...]:
                    return (s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if stable(before) != stable(after) or len(data) != before.st_size:
                    raise ValueError("changed")
                return data, (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        finally:
            os.close(fd)

    try:
        signatures = {}
        for path in paths:
            if time.monotonic() - started > 20:
                raise ValueError("deadline")
            data, signature = one(path)
            total += len(data)
            if total > max_bytes:
                raise ValueError("limit")
            signatures[path] = (signature, hashlib.sha256(data).hexdigest())
            result.append({"path": path, "data": base64.b64encode(data).decode("ascii")})
        for path in paths:
            data, signature = one(path)
            if signatures[path] != (signature, hashlib.sha256(data).hexdigest()):
                raise ValueError("changed")
        return result
    finally:
        os.close(root_fd)


if __name__ == "__main__":
    try:
        arguments = json.loads(sys.argv[1])
        print(
            json.dumps(
                {"files": read("/workspace", arguments["files"], arguments["max_bytes"], arguments["max_file_bytes"])}
            )
        )
    except Exception:
        print(json.dumps({"error": "Selected workspace files are unsafe, unavailable or changed"}))
        sys.exit(1)
