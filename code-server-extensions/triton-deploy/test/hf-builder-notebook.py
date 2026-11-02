"""Verify the real public API in Python and a live notebook kernel."""

import json
import os
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager
from triton_hf_builder import generate_repository, inspect_model


def verify(expected: str, existing_kernel: str | None = None) -> None:
    assert callable(generate_repository) and callable(inspect_model)
    assert version("triton-hf-builder") == expected
    with tempfile.TemporaryDirectory(prefix="hf-builder-notebook-") as temporary:
        root = Path(temporary)
        kernel_name = existing_kernel or "hf-builder-compatibility"
        if existing_kernel:
            manager = KernelManager(kernel_name=kernel_name)
        else:
            subprocess.run([
                sys.executable, "-m", "ipykernel", "install", "--prefix", str(root),
                "--name", kernel_name,
            ], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            manager = KernelManager(kernel_name=kernel_name, kernel_spec_manager=KernelSpecManager(
                kernel_dirs=[str(root / "share/jupyter/kernels")],
            ))
        manager.start_kernel(env={**os.environ, "IPYTHONDIR": str(root / "ipython")})
        client = manager.blocking_client()
        client.start_channels()
        try:
            client.wait_for_ready(timeout=60)
            message_id = client.execute(
                "from triton_hf_builder import generate_repository, inspect_model; "
                "from importlib.metadata import version; "
                "assert callable(generate_repository) and callable(inspect_model); "
                f"assert version('triton-hf-builder') == {expected!r}"
            )
            deadline = time.monotonic() + 60
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Notebook kernel did not reply to the compatibility cell")
                reply = client.get_shell_msg(timeout=remaining)
                if (reply.get("parent_header", {}).get("msg_id") == message_id
                        and reply.get("header", {}).get("msg_type") == "execute_reply"):
                    break
            assert reply["content"]["status"] == "ok", reply["content"]
        finally:
            manager.shutdown_kernel(now=True)
            client.stop_channels()
    print(json.dumps({"version": expected, "python": True, "notebook": True}))


if __name__ == "__main__":
    verify(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
