"""Pydantic/SQLModel schemas for dashboard alert data.

Defines the outgoing DTO used by the dashboard API:
  ``DashboardAlertDTO`` — read-only model returned by
    ``GET /api/dashboard/alerts``.  Fields include the alert ``id``,
    optional ``instance_id`` and ``instance_name``, a display ``icon``,
    human-readable ``label``, a ``tone`` (severity indicator), and
    ``created_at`` timestamp.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel


class DashboardAlertDTO(SQLModel):
    id: int
    instance_id: Optional[int] = None
    instance_name: Optional[str] = None
    icon: str
    label: str
    tone: str
    created_at: datetime
