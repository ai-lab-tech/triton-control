"""Schemas for admin-visible application error events."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel


class ErrorEventDTO(SQLModel):
    id: int
    source: str
    level: str
    message: str
    detail: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    user_email: Optional[str] = None
    user_id: Optional[int] = None
    user_agent: Optional[str] = None
    created_at: datetime


class FrontendErrorEventRequest(SQLModel):
    level: str = "ERROR"
    message: str
    detail: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    user_agent: Optional[str] = None
