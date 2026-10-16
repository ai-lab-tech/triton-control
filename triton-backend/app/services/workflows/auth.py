"""Backend-only Kubernetes identity for the Argo client-auth API."""

from __future__ import annotations

import os
from pathlib import Path

from app.exceptions import ServiceUnavailableError


def authorization_headers() -> dict[str, str]:
    # Read every time: Kubernetes rotates the projected, pod-bound token.
    # Never forward the browser's token or persist this credential in a Secret.
    path = os.getenv(
        "ARGO_WORKFLOWS_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ).strip()
    try:
        token = Path(path).read_text(encoding="utf-8").strip() if path else ""
    except OSError as exc:
        raise ServiceUnavailableError("Argo backend ServiceAccount token is unavailable") from exc
    if not token or any(character.isspace() for character in token):
        raise ServiceUnavailableError("Argo backend ServiceAccount token is invalid")
    return {"authorization": f"Bearer {token}"}
