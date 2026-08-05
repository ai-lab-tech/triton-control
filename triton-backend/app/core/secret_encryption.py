"""Authenticated encryption for database-managed email secrets."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    raw = (os.getenv("EMAIL_SECRET_ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise ValueError("EMAIL_SECRET_ENCRYPTION_KEY is required for database-managed SMTP passwords")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_email_secret(secret: str) -> str:
    if not secret:
        return ""
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_email_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Stored SMTP password cannot be decrypted") from exc

