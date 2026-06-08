"""Local-authentication JWT utilities.

Handles issuing and verifying HS256 JWTs for the local (email/password)
authentication strategy:
  ``issue_access_token``  — Creates a signed JWT from a claims dict.
  ``verify_access_token`` — Validates signature and expiry; returns claims.
  ``verify_password``     — Constant-time password verification (PBKDF2).
  ``hash_password``       — Re-exported from ``app.core.crypto``.

The signing secret is read from the ``JWT_SECRET`` environment variable
(falls back to ``SESSION_SECRET`` for backwards compatibility).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from jose import JWTError, jwt

from app.core.crypto import hash_password as hash_password


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, hash_b64 = encoded_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            int(iterations),
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET") or os.getenv("SESSION_SECRET") or "change-me-in-production"


def issue_access_token(user: Dict[str, Any], expires_minutes: int = 60) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.get("email"),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role", "User"),
        "auth_provider": user.get("auth_provider", "local"),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_access_token(token: str) -> Dict[str, Any]:
    try:
        claims = jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
        return dict(claims)
    except JWTError as e:
        raise ValueError(f"Local token verification failed: {e}")
