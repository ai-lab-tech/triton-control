"""HTTP endpoints for reusable user-owned S3 deployment profiles."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import CreateS3ProfileRequest, S3ProfileDTO, UpdateS3ProfileRequest
from app.services.storage import s3_profiles as s3_profile_service

router = APIRouter(prefix="/api/s3-profiles", tags=["s3-profiles"])


@router.get("", response_model=list[S3ProfileDTO])
@translate_app_errors
def list_s3_profiles(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[S3ProfileDTO]:
    return s3_profile_service.list_profiles(session, claims)


@router.post("", response_model=S3ProfileDTO)
@translate_app_errors
def create_s3_profile(
    request: CreateS3ProfileRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3ProfileDTO:
    return s3_profile_service.create_profile(session, claims, request)


@router.put("/{profile_id}", response_model=S3ProfileDTO)
@translate_app_errors
def update_s3_profile(
    profile_id: int,
    request: UpdateS3ProfileRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3ProfileDTO:
    return s3_profile_service.update_profile(session, claims, profile_id, request)


@router.delete("/{profile_id}")
@translate_app_errors
def delete_s3_profile(
    profile_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, str]:
    return s3_profile_service.delete_profile(session, claims, profile_id)
