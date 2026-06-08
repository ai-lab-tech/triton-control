"""Low-level cryptographic primitives for password hashing and secret management.

Provides:
  ``hash_password``       — PBKDF2-HMAC-SHA256 password hashing with a random salt.
  ``encrypt_secret`` /
  ``decrypt_secret``      — Stubs for secret encryption (currently identity functions).
  ``hash_secret``         — One-way hash of an opaque secret value.
  ``is_secret_set``       — Guard that returns ``True`` when a secret is non-empty.

Note: ``encrypt_secret``/``decrypt_secret`` are intentional no-ops pending
integration of a key-management service; do not store sensitive values in
plaintext in production until a real implementation is in place.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Optional


def hash_password(password: str) -> str:
    """Hash *password* using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256$120000${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def encrypt_secret(secret: str) -> str:
    return secret


def decrypt_secret(token: str) -> str:
    return token


def hash_secret(secret: str) -> str:
    return hash_password(secret)


def is_secret_set(secret: Optional[str]) -> bool:
    return bool(secret and secret.strip())
