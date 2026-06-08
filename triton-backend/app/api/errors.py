"""API-layer error translation utilities.

The ``translate_app_errors`` decorator wraps FastAPI path-operation functions
(both sync and async) so that ``AppError`` subclasses raised inside service or
repository code are automatically converted to ``HTTPException``.

This keeps ``HTTPException`` out of the service/domain layer while still
delivering the correct HTTP status codes to clients.

Example::

    from app.api.errors import translate_app_errors

    @router.delete("/users/{user_id}")
    @translate_app_errors
    def delete_user(user_id: int, ...):
        return user_service.delete_user(session, claims, user_id)
"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable

from fastapi import HTTPException

from app.exceptions import AppError


def translate_app_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that translates ``AppError`` → ``HTTPException``.

    Works transparently for both synchronous and asynchronous path-operation
    functions.
    """
    if asyncio.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except AppError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return async_wrapper

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except AppError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return sync_wrapper
