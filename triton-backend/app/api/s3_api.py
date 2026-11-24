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
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import AfterValidator
from sqlmodel import Session
from starlette.types import Receive, Scope, Send

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    InstanceS3ConfigDTO,
    S3DeleteResponse,
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


class _S3DownloadResponse(StreamingResponse):
    def __init__(self, obj: dict[str, Any], filename: str) -> None:
        self.body_stream = obj["Body"]
        headers = {"Content-Length": str(obj["ContentLength"])} if "ContentLength" in obj else {}
        headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(filename, safe="")
        headers["X-Accel-Buffering"] = "no"
        headers["Cache-Control"] = "private, no-store"
        super().__init__(
            iter(lambda: self.body_stream.read(1024 * 1024), b""),
            media_type=obj.get("ContentType") or "application/octet-stream",
            headers=headers,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.body_stream.close()


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
    obj = s3_service.open_instance_s3_object(session, claims, instance_id, path)
    session.close()
    return _S3DownloadResponse(obj, path.rsplit("/", 1)[-1])


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


@router.delete("/{instance_id}/s3/content", response_model=S3DeleteResponse)
@translate_app_errors
def delete_instance_s3_content(
    instance_id: int,
    path: S3Path,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> S3DeleteResponse:
    return s3_service.delete_instance_s3_content(session, claims, instance_id, path)
