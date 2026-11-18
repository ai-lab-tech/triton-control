"""Strict capture exclusions, shared by model and selected workspace providers."""

import re

from app.exceptions import BadRequestError

DENIED = {
    ".git",
    ".aws",
    ".ssh",
    ".kube",
    ".docker",
    ".config",
    ".local",
    ".cache",
    ".triton-control",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
}


def allowed(path: str) -> bool:
    for part in path.lower().split("/"):
        if (
            part in DENIED
            or part in {"credentials", "kubeconfig", ".npmrc", ".pypirc", ".netrc", "id_rsa", "id_ed25519"}
            or part == ".env"
            or part.startswith(".env.")
            or part.endswith((".pem", ".key", ".p12", ".pfx", ".jks"))
        ):
            return False
    return True


def reject_secrets(data: bytes) -> None:
    if re.search(rb"-----BEGIN ([A-Z ]*PRIVATE KEY)-----|(?:AKIA|ASIA)[A-Z0-9]{16}", data):
        raise BadRequestError("Selected files contain credential material")
