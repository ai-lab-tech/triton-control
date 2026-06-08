"""Dashboard HTTP endpoints.

Exposes a single router mounted at ``/api/dashboard``:
  ``GET /api/dashboard/alerts`` — returns the persisted alert snapshots
                                   visible to the authenticated caller.
                                   Admins see all alerts; non-admins see
                                   only alerts belonging to their assigned
                                   instances.

Business logic is delegated to ``services/dashboard``.
"""

from typing import Any, List

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import DashboardAlertDTO
from app.services import dashboard as dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/alerts", response_model=List[DashboardAlertDTO])
@translate_app_errors
def list_dashboard_alerts(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> List[DashboardAlertDTO]:
    """Return persisted dashboard alerts visible to the current user."""
    return dashboard_service.list_dashboard_alerts(session, claims)
