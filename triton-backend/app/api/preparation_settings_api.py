"""Admin-only preparation limit settings."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.preparation_settings import PreparationLimits, PreparationSettings
from app.services import preparation_settings

router = APIRouter(prefix="/api/settings/model-preparation", tags=["settings"])


@router.get("", response_model=PreparationSettings)
@translate_app_errors
def read(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> PreparationSettings:
    return preparation_settings.read(session, claims)


@router.put("", response_model=PreparationSettings)
@translate_app_errors
def update(request: PreparationLimits, session: Session = Depends(get_session),
           claims: dict[str, Any] = Depends(get_claims)) -> PreparationSettings:
    return preparation_settings.update(session, claims, request)


@router.delete("", response_model=PreparationSettings)
@translate_app_errors
def reset(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> PreparationSettings:
    return preparation_settings.update(session, claims, None)
