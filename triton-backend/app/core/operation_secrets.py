"""Authenticated encryption for restartable operations; no plaintext fallback."""

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.exceptions import BadRequestError


def key() -> bytes:
    value = os.getenv("OPERATION_SECRET_ENCRYPTION_KEY") or os.getenv("SESSION_SECRET")
    if not value or value == "change-me-in-production":
        raise BadRequestError("Configure a stable OPERATION_SECRET_ENCRYPTION_KEY or SESSION_SECRET")
    return hashlib.sha256(value.encode()).digest()


def encode(value: dict[str, Any]) -> str:
    return Fernet(base64.urlsafe_b64encode(key())).encrypt(json.dumps(value, allow_nan=False).encode()).decode()


def decode(value: str) -> dict[str, Any]:
    try:
        result: dict[str, Any] = json.loads(Fernet(base64.urlsafe_b64encode(key())).decrypt(value.encode()))
        return result
    except (InvalidToken, ValueError):
        raise BadRequestError("Deployment inputs cannot be decrypted; restore the operation encryption key") from None


def fingerprint(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hmac.new(key(), canonical.encode(), hashlib.sha256).hexdigest()
