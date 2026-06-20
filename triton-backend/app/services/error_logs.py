"""Application error event persistence and admin queries."""

from __future__ import annotations

import traceback
from typing import Any

from fastapi import Request
from sqlalchemy import delete
from sqlmodel import Session, col, desc, select

from app.core.access_control import require_admin
from app.db.entities import ErrorEventEntity
from app.schemas import ErrorEventDTO, FrontendErrorEventRequest

MAX_MESSAGE_LENGTH = 1000
MAX_DETAIL_LENGTH = 6000
MAX_USER_AGENT_LENGTH = 500
VALID_SOURCES = {"frontend", "backend"}
VALID_LEVELS = {"ERROR", "CRITICAL", "WARNING"}
SENSITIVE_MARKERS = ("authorization", "token", "secret", "password", "cookie")


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 15]}... [truncated]"


def _sanitize_text(value: str | None, limit: int) -> str | None:
    text = _truncate(value, limit)
    if not text:
        return text
    lines = []
    for line in text.splitlines():
        lower = line.lower()
        if any(marker in lower for marker in SENSITIVE_MARKERS):
            lines.append("[redacted]")
        else:
            lines.append(line)
    return "\n".join(lines)


def _normalize_level(level: str | None) -> str:
    normalized = (level or "ERROR").strip().upper()
    return normalized if normalized in VALID_LEVELS else "ERROR"


def _dto(row: ErrorEventEntity) -> ErrorEventDTO:
    return ErrorEventDTO(
        id=row.id or 0,
        source=row.source,
        level=row.level,
        message=row.message,
        detail=row.detail,
        path=row.path,
        method=row.method,
        status_code=row.status_code,
        user_email=row.user_email,
        user_id=row.user_id,
        user_agent=row.user_agent,
        created_at=row.created_at,
    )


def list_error_events(
    session: Session,
    claims: dict[str, Any],
    source: str | None = None,
    limit: int = 100,
) -> list[ErrorEventDTO]:
    """Return recent error events for admins."""
    require_admin(claims)
    bounded_limit = max(1, min(limit, 500))
    statement = select(ErrorEventEntity)
    if source:
        normalized_source = source.strip().lower()
        if normalized_source in VALID_SOURCES:
            statement = statement.where(ErrorEventEntity.source == normalized_source)
    statement = statement.order_by(desc(ErrorEventEntity.created_at)).limit(bounded_limit)
    return [_dto(row) for row in session.exec(statement).all()]


def create_frontend_error_event(
    session: Session,
    claims: dict[str, Any],
    payload: FrontendErrorEventRequest,
) -> ErrorEventDTO:
    """Persist a frontend-reported error event for the authenticated user."""
    row = ErrorEventEntity(
        source="frontend",
        level=_normalize_level(payload.level),
        message=_sanitize_text(payload.message, MAX_MESSAGE_LENGTH) or "Frontend error",
        detail=_sanitize_text(payload.detail, MAX_DETAIL_LENGTH),
        path=_truncate(payload.path, 500),
        method=_truncate(payload.method, 20),
        status_code=payload.status_code,
        user_email=_truncate(str(claims.get("email") or ""), 320) or None,
        user_id=claims.get("user_id"),
        user_agent=_truncate(payload.user_agent, MAX_USER_AGENT_LENGTH),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    _prune_old_events(session)
    return _dto(row)


def create_backend_exception_event(session: Session, request: Request, exc: Exception) -> None:
    """Best-effort persistence for unhandled backend exceptions."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    row = ErrorEventEntity(
        source="backend",
        level="ERROR",
        message=_sanitize_text(str(exc) or exc.__class__.__name__, MAX_MESSAGE_LENGTH) or exc.__class__.__name__,
        detail=_sanitize_text(detail, MAX_DETAIL_LENGTH),
        path=_truncate(str(request.url.path), 500),
        method=request.method,
        status_code=500,
        user_agent=_truncate(request.headers.get("user-agent"), MAX_USER_AGENT_LENGTH),
    )
    session.add(row)
    session.commit()
    _prune_old_events(session)


def create_backend_log_event(
    session: Session,
    logger_name: str,
    level: str,
    message: str,
    detail: str | None = None,
) -> None:
    """Persist an application logger error record."""
    row = ErrorEventEntity(
        source="backend",
        level=_normalize_level(level),
        message=_sanitize_text(message, MAX_MESSAGE_LENGTH) or "Backend log error",
        detail=_sanitize_text(detail, MAX_DETAIL_LENGTH),
        path=_truncate(logger_name, 500),
    )
    session.add(row)
    session.commit()
    _prune_old_events(session)


def _prune_old_events(session: Session, keep: int = 1000) -> None:
    ids_to_keep = list(
        session.exec(select(ErrorEventEntity.id).order_by(desc(ErrorEventEntity.created_at)).limit(keep))
    )
    if not ids_to_keep:
        return
    session.exec(delete(ErrorEventEntity).where(col(ErrorEventEntity.id).not_in(ids_to_keep)))
    session.commit()
