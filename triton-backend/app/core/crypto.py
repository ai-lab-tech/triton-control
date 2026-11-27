"""Password hashing and authenticated encryption for stored S3 credentials."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Optional

from app.db.s3_credentials import decrypt_secret as decrypt_secret
from app.db.s3_credentials import encrypt_secret as encrypt_secret


def hash_password(password: str) -> str:
    """Hash *password* using PBKDF2-HMAC-SHA256 with a random salt."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256$120000${base64.b64encode(salt).decode()}${base64.b64encode(derived).decode()}"


def hash_secret(secret: str) -> str:
    return hash_password(secret)


def is_secret_set(secret: Optional[str]) -> bool:
    return bool(secret and secret.strip())
