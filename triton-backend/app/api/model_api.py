"""HTTP endpoints for Triton model repository operations.

Mounts a sub-router under ``/api/instances`` and exposes:
  ``GET  /{id}/models``                            — list the live model
                                                      repository index.
  ``GET  /{id}/models/{name}/versions/{v}/config`` — fetch parsed model config
                                                      (protobuf → JSON).
  ``POST /{id}/models/{name}/load``                — load a model into Triton.
  ``POST /{id}/models/{name}/unload``              — unload a model from Triton.
  ``POST /{id}/models/{name}/versions/{v}/infer``  — proxy a raw inference
                                                      request to Triton.

All endpoints are async, JWT-protected, and delegate to ``services/triton/models``.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi import Path as PathParam
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import ModelRepositoryActionResponse, TritonRepositoryModelDTO
from app.services.triton import models as model_service

router = APIRouter(prefix="/api/instances", tags=["instances"])


@router.get("/{instance_id}/models", response_model=list[TritonRepositoryModelDTO])
@translate_app_errors
async def get_instance_models(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> list[TritonRepositoryModelDTO]:
    """Return the live Triton model repository index for an instance."""
    return await model_service.list_models(session, claims, instance_id)


@router.get("/{instance_id}/models/{model_name}/versions/{version}/config")
@translate_app_errors
async def get_instance_model_config(
    instance_id: int,
    model_name: Annotated[str, PathParam(min_length=1)],
    version: Annotated[str, PathParam(min_length=1)],
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Any:
    """Proxy the live Triton model config for a specific model version."""
    return await model_service.get_model_config(session, claims, instance_id, model_name, version)


@router.post("/{instance_id}/models/{model_name}/load", response_model=ModelRepositoryActionResponse)
@translate_app_errors
async def load_instance_model(
    instance_id: int,
    model_name: Annotated[str, PathParam(min_length=1)],
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelRepositoryActionResponse:
    """Trigger explicit load for a model via Triton's repository API."""
    return await model_service.load_model(session, claims, instance_id, model_name)


@router.post("/{instance_id}/models/{model_name}/unload", response_model=ModelRepositoryActionResponse)
@translate_app_errors
async def unload_instance_model(
    instance_id: int,
    model_name: Annotated[str, PathParam(min_length=1)],
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> ModelRepositoryActionResponse:
    """Trigger explicit unload for a model via Triton's repository API."""
    return await model_service.unload_model(session, claims, instance_id, model_name)


@router.post("/{instance_id}/models/{model_name}/versions/{version}/infer")
@translate_app_errors
async def infer_instance_model(
    instance_id: int,
    model_name: Annotated[str, PathParam(min_length=1)],
    version: Annotated[str, PathParam(min_length=1)],
    _payload: Annotated[Any, Body(title="Payload", description="Triton inference request body.")],
    request: Request,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    """Proxy a Triton inference request for a specific model version."""
    payload_bytes = await request.body()
    content_type = request.headers.get("content-type", "application/json")
    return await model_service.infer_model(
        session,
        claims,
        instance_id,
        model_name,
        version,
        payload_bytes,
        content_type,
    )
