"""Ephemeral navigation handoff from Development extensions to the web UI."""

from __future__ import annotations

from threading import Lock
from typing import Any

_pending_instance_by_user: dict[str, int] = {}
_lock = Lock()


def notify_deployment_created(claims: dict[str, Any], instance_id: int) -> None:
    key = _claims_key(claims)
    if not key or instance_id <= 0:
        return
    with _lock:
        _pending_instance_by_user[key] = instance_id


def consume_deployment_created(claims: dict[str, Any]) -> int | None:
    key = _claims_key(claims)
    if not key:
        return None
    with _lock:
        return _pending_instance_by_user.pop(key, None)


def _claims_key(claims: dict[str, Any]) -> str:
    user_id = claims.get("user_id")
    if user_id is not None:
        return f"id:{user_id}"
    email = (claims.get("email") or "").strip().lower()
    if email:
        return f"email:{email}"
    subject = (claims.get("sub") or "").strip()
    return f"sub:{subject}" if subject else ""
