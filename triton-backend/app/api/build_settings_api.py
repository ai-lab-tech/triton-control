"""Admin settings for the serving image build policy."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.build_policy import BuildPolicy, BuildPolicySettings
from app.services.builds import settings


@translate_app_errors
def admin_session(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> Session:
    settings.authorize(session, claims)
    return session


router = APIRouter(prefix="/api/settings/serving-image-builds", tags=["settings"])


@router.get("", response_model=BuildPolicySettings)
@translate_app_errors
def read(session: Session = Depends(admin_session)) -> BuildPolicySettings:
    return settings.read(session)


@router.put("", response_model=BuildPolicySettings)
@translate_app_errors
def update(policy: BuildPolicy, session: Session = Depends(admin_session)) -> BuildPolicySettings:
    return settings.update(session, policy)


@router.delete("", response_model=BuildPolicySettings)
@translate_app_errors
def reset(session: Session = Depends(admin_session)) -> BuildPolicySettings:
    return settings.update(session, None)
