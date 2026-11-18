"""Capture only explicitly selected files from a UID-verified owned workspace."""

import base64
import hashlib
import json
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

from kubernetes import client  # type: ignore[import-untyped]
from kubernetes.stream import stream  # type: ignore[import-untyped]

from app.exceptions import BadRequestError, ConflictError
from app.schemas.lineage import FileEntry, InventoryV1
from app.services.deployment.pod_provider import TIMEOUT, controlled_by
from app.services.kubernetes_client import api_client
from app.services.snapshots.safety import allowed, reject_secrets

MAX_BYTES = 25 * 1024**2
MAX_FILE_BYTES = 10 * 1024**2


class WorkspaceProvider:
    def capture(self, namespace: str, name: str, selected: tuple[str, ...], destination: Path) -> InventoryV1:
        if any(not allowed(path) for path in selected):
            raise BadRequestError("Select project files only; credential/runtime paths cannot be captured")
        with api_client() as api:
            core, apps = client.CoreV1Api(api), client.AppsV1Api(api)
            stateful = api.sanitize_for_serialization(
                apps.read_namespaced_stateful_set(name, namespace, _request_timeout=TIMEOUT)
            )
            uid = stateful["metadata"]["uid"]
            # StatefulSet's stable ordinal name is only a lookup, never authorization.
            pod = api.sanitize_for_serialization(
                core.read_namespaced_pod(name + "-0", namespace, _request_timeout=TIMEOUT)
            )
            if not controlled_by(pod, "StatefulSet", uid) or pod["metadata"].get("deletionTimestamp"):
                raise ConflictError("Workspace pod is unrelated or terminating")
            pod_uid = pod["metadata"]["uid"]
            script = files("app.services.snapshots").joinpath("workspace_reader.py").read_text()
            arguments = json.dumps({"files": selected, "max_bytes": MAX_BYTES, "max_file_bytes": MAX_FILE_BYTES})
            connection = stream(
                core.connect_get_namespaced_pod_exec,
                pod["metadata"]["name"],
                namespace,
                command=["python3", "-I", "-S", "-c", script, arguments],
                container="code-server",
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False,
                _request_timeout=TIMEOUT,
            )
            try:
                output = self.collect(connection)
            finally:
                connection.close()
            after = api.sanitize_for_serialization(
                core.read_namespaced_pod(pod["metadata"]["name"], namespace, _request_timeout=TIMEOUT)
            )
            current = api.sanitize_for_serialization(
                apps.read_namespaced_stateful_set(name, namespace, _request_timeout=TIMEOUT)
            )
            if (
                after["metadata"]["uid"] != pod_uid
                or not controlled_by(after, "StatefulSet", uid)
                or current["metadata"]["uid"] != uid
            ):
                raise ConflictError("Workspace was replaced during capture")
        return decode(output, selected, destination)

    def collect(self, connection: Any) -> bytes:
        chunks = []
        size = 0
        deadline = time.monotonic() + 30
        while connection.is_open():
            if time.monotonic() > deadline:
                raise ConflictError("Workspace capture deadline exceeded")
            connection.update(timeout=1)
            if connection.peek_stderr():
                # Never persist raw stderr, which can expose environment credentials.
                raise BadRequestError("Workspace capture reader failed")
            if connection.peek_stdout():
                chunk = connection.read_stdout()
                size += len(chunk)
                if size > MAX_BYTES * 2 + 1024**2:
                    raise BadRequestError("Workspace response exceeds capture limits")
                chunks.append(chunk)
        return "".join(chunks).encode("utf-8")


def decode(raw: bytes, selected: tuple[str, ...], destination: Path) -> InventoryV1:
    try:
        document = json.loads(raw)
        rows = document["files"]
        if len(rows) != len(selected) or {row["path"] for row in rows} != set(selected):
            raise ValueError("selection")
        inventory = []
        total = 0
        for row in rows:
            data = base64.b64decode(row["data"], validate=True)
            total += len(data)
            if total > MAX_BYTES or len(data) > MAX_FILE_BYTES:
                raise ValueError("limit")
            reject_secrets(data)
            path = ".workspace/" + row["path"]
            entry = FileEntry(path=path, size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())
            if not allowed(row["path"]):
                raise ValueError("excluded")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inventory.append(entry)
        return InventoryV1(files=tuple(inventory))
    except (ValueError, TypeError, KeyError):
        raise BadRequestError("Workspace response is incomplete, unsafe or invalid") from None
