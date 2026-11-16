"""Build context admission and bounded streaming upload; no executor API."""

import tempfile
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.exceptions import BadRequestError
from app.schemas.build_contexts import BuildContextRequest, BuildContextResponse
from app.services.builds import contexts

router = APIRouter(prefix="/api/build-contexts", tags=["build-contexts"])


@router.post("", response_model=BuildContextResponse, status_code=202)
@translate_app_errors
def submit(
    request: BuildContextRequest, response: Response,
    idempotency_key: str = Header(min_length=1, max_length=128),
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildContextResponse:
    result = contexts.submit(session, claims, request, idempotency_key)
    response.headers["Location"] = result.operation.status_url
    return result


@router.get("/{context_id}", response_model=BuildContextResponse)
@translate_app_errors
def read(
    context_id: str, session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildContextResponse:
    return contexts.read(session, claims, context_id)


@router.put(
    "/{context_id}/archive", response_model=BuildContextResponse, status_code=202,
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/x-tar": {"schema": {"type": "string", "format": "binary"}},
    }}},
)
@translate_app_errors
async def upload(
    context_id: str, request: Request, response: Response,
    session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims),
) -> BuildContextResponse:
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-tar":
        raise BadRequestError("Upload an uncompressed POSIX tar with Content-Type application/x-tar")
    token, expected, limit = await anyio.to_thread.run_sync(contexts.begin_upload, session, claims, context_id)
    try:
        session.rollback()  # Do not retain a DB transaction while reading the network.
        with tempfile.TemporaryFile() as stream:
            count = 0
            try:
                with anyio.fail_after(120):
                    async for chunk in request.stream():
                        count += len(chunk)
                        if count > min(expected.archive_bytes, limit):
                            raise BadRequestError("Build archive exceeds declared size or policy limit")
                        await anyio.to_thread.run_sync(stream.write, chunk)
            except TimeoutError:
                raise BadRequestError("Build archive upload deadline exceeded") from None
            if count != expected.archive_bytes:
                raise BadRequestError("Incomplete build archive upload")
            result = await anyio.to_thread.run_sync(contexts.finish_upload, session, claims, context_id, token, stream)
        response.headers["Location"] = result.operation.status_url
        return result
    finally:
        # Shield cleanup on disconnect/cancellation. A persisted S3 write intent
        # remains recoverable; an incomplete body releases its upload reservation.
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(contexts.abort_upload, session, context_id, token)
