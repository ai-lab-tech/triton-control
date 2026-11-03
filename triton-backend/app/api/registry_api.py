"""Admin-only central image pull credentials; never return secret contents."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.registry import RegistryUpdate
from app.services import registry_settings

router = APIRouter(prefix="/api/settings/registry", tags=["settings"])


@router.get("")
@translate_app_errors
def get_registry_settings(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, bool]:
    return registry_settings.get_settings(session, claims)


@router.put("")
@translate_app_errors
def put_registry_settings(
    request: RegistryUpdate,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, bool]:
    return registry_settings.update_settings(request, session, claims)
