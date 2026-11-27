"""Business logic for the dashboard feature.

Provides:
  ``list_dashboard_alerts(session, claims)`` — returns the list of persisted
    ``DashboardAlertDTO`` objects visible to the caller.  Admins receive all
    alerts; non-admin users receive only alerts belonging to instances in
    their ``assigned_instances`` list.  Delegates to the
    ``repositories/dashboard_alerts`` module for the actual DB query.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from app.core.access_control import is_admin
from app.core.identity import require_user_entity
from app.mappers import dashboard_alert_entity_to_dto
from app.repositories import dashboard_alerts
from app.schemas import DashboardAlertDTO


def list_dashboard_alerts(session: Session, claims: dict[str, Any]) -> list[DashboardAlertDTO]:
    assigned: list[str] | None = None
    if not is_admin(claims):
        user = require_user_entity(session, claims)
        assigned = [name for name in (user.assigned_instances or []) if name]

    rows = dashboard_alerts.list_visible(session, assigned)
    return [dashboard_alert_entity_to_dto(row) for row in rows]
