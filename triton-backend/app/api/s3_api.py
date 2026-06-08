"""HTTP endpoints for S3 model-repository access.

Mounts a sub-router under ``/api/instances`` and exposes:
  ``GET  /{id}/s3``         — retrieve the instance's S3 configuration.
  ``PUT  /{id}/s3``         — update the instance's S3 configuration.
  ``GET  /{id}/s3/list``    — list objects under a given S3 prefix.
  ``GET  /{id}/s3/files``   — download the content of a single S3 file.
  ``PUT  /{id}/s3/files``   — upload / overwrite a single S3 file.

Incoming ``path`` query parameters are sanitised by ``_clean_s3_path``
(strips leading slashes) before reaching the service layer.
Business logic is delegated to ``services/storage/s3``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from pydantic import AfterValidator
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    InstanceS3ConfigDTO,
    S3FileContentResponse,
    S3FileWriteResponse,
    S3ListResponse,
    UpdateInstanceS3Request,
)
from app.services.storage import s3 as s3_service


def _clean_s3_path(v: str) -> str:
    cleaned = v.strip().lstrip("/")
    if not cleaned:
        raise ValueError("path must not be empty")
    return cleaned


S3Path = Annotated[str, Query(description="S3 object path"), AfterValidator(_clean_s3_path)]


router = APIRouter(prefix="/api/instances", tags=["instances"])


@router.get("/{instance_id}/s3", response_model=InstanceS3ConfigDTO)
@translate_app_errors
def get_instance_s3(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> InstanceS3ConfigDTO:
    return s3_service.get_instance_s3(session, claims, instance_id)


@router.put("/{instance_id}/s3", response_model=InstanceS3ConfigDTO)
@translate_app_errors
def update_instance_s3(
    instance_id: int,
    request: UpdateInstanceS3Request,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> InstanceS3ConfigDTO:
    return s3_service.update_instance_s3(session, claims, instance_id, request)


@router.get("/{instance_id}/s3/list", response_model=S3ListResponse)
@translate_app_errors
def list_instance_s3(
    instance_id: int,
    prefix: str = "",
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3ListResponse:
    return s3_service.list_instance_s3(session, claims, instance_id, prefix)


@router.get("/{instance_id}/s3/content", response_model=S3FileContentResponse)
@translate_app_errors
def get_instance_s3_content(
    instance_id: int,
    path: S3Path,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3FileContentResponse:
    return s3_service.get_instance_s3_content(session, claims, instance_id, path)


@router.get(
    "/{instance_id}/s3/content/raw",
    response_class=Response,
    responses={
        200: {
            "description": "Raw object bytes",
            "content": {
                "application/octet-stream": {
                    "schema": {"type": "string", "format": "binary"},
                }
            },
        }
    },
)
@translate_app_errors
def get_instance_s3_content_raw(
    instance_id: int,
    path: S3Path,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    payload, content_type = s3_service.get_instance_s3_object_bytes(session, claims, instance_id, path)
    return Response(content=payload, media_type=content_type or "application/octet-stream")


@router.put("/{instance_id}/s3/content", response_model=S3FileWriteResponse)
@translate_app_errors
def put_instance_s3_content(
    instance_id: int,
    path: S3Path,
    content: bytes = Body(..., media_type="application/octet-stream"),
    content_type: str | None = None,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3FileWriteResponse:
    return s3_service.put_instance_s3_content(
        session,
        claims,
        instance_id,
        path,
        content,
        content_type,
    )
