"""Pure role-based authorization guards shared across the application.

Provides two functions with no I/O or DB dependencies:
  ``is_admin(claims)``     — returns ``True`` when the claims dict carries
                              the ``admin`` role (case-insensitive).
  ``require_admin(claims)``— raises ``ForbiddenError`` immediately if the
                              caller is not an admin; intended as a guard
                              at the top of admin-only service functions.

By keeping authorization logic here — separate from FastAPI dependencies
and service code — it remains independently testable and reusable.
"""

from __future__ import annotations

from typing import Any

from app.exceptions import ForbiddenError


def _role(claims: dict[str, Any]) -> str:
    return (claims.get("role") or "").strip().lower()


def is_admin(claims: dict[str, Any]) -> bool:
    """Return True when the claims carry the 'admin' role."""
    return _role(claims) == "admin"


def is_member_or_admin(claims: dict[str, Any]) -> bool:
    """Return True when the claims can mutate assigned instance resources."""
    return _role(claims) in {"admin", "member"}


def require_admin(claims: dict[str, Any]) -> None:
    """Raise ForbiddenError unless the claims carry the 'admin' role."""
    if not is_admin(claims):
        raise ForbiddenError("Admin role required")


def require_member_or_admin(claims: dict[str, Any]) -> None:
    """Raise ForbiddenError unless the claims can perform write operations."""
    if not is_member_or_admin(claims):
        raise ForbiddenError("Member or admin role required")
