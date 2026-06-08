"""Domain-level exceptions for the triton-backend.

These exceptions carry no HTTP semantics.  The API layer (``app/api/``) is
responsible for translating them into ``HTTPException`` via the
``translate_app_errors`` decorator defined in ``app/api/errors.py``.

Usage in services::

    from app.exceptions import NotFoundError, ForbiddenError

    def get_user(session, user_id):
        row = users.find_by_id(session, user_id)
        if not row:
            raise NotFoundError("User not found")
        return row

Usage in API layer (automatic via decorator)::

    from app.api.errors import translate_app_errors

    @router.get("/users/{user_id}")
    @translate_app_errors
    def get_user_endpoint(user_id: int, ...):
        return user_service.get_user(session, user_id)
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all domain exceptions.

    Subclasses declare a ``status_code`` that the API translation layer uses
    when converting to ``HTTPException``.
    """

    status_code: int = 500

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(AppError):
    """Requested resource does not exist (→ HTTP 404)."""

    status_code = 404


class ForbiddenError(AppError):
    """Caller is authenticated but lacks permission (→ HTTP 403)."""

    status_code = 403


class UnauthorizedError(AppError):
    """Caller could not be identified or authenticated (→ HTTP 401)."""

    status_code = 401


class ConflictError(AppError):
    """Resource already exists or state conflict (→ HTTP 409)."""

    status_code = 409


class BadRequestError(AppError):
    """Invalid input supplied by the caller (→ HTTP 400)."""

    status_code = 400


class ServiceUnavailableError(AppError):
    """Downstream service is not reachable or not ready (→ HTTP 503)."""

    status_code = 503


class BadGatewayError(AppError):
    """Upstream service returned an unexpected error (→ HTTP 502)."""

    status_code = 502


class UnsupportedMediaTypeError(AppError):
    """Content could not be decoded or has an unsupported format (→ HTTP 415)."""

    status_code = 415


class UnprocessableEntityError(AppError):
    """Request is syntactically valid but semantically invalid (→ HTTP 422)."""

    status_code = 422


class InternalError(AppError):
    """Unexpected server-side error (→ HTTP 500)."""

    status_code = 500
