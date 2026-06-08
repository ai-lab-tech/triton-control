"""HTTP endpoints for Triton instance management (CRUD).

Mounts a router at ``/api/instances`` and exposes:
  ``POST   /api/instances``       — register a new Triton instance (async;
                                     connects live to Triton on creation).
  ``GET    /api/instances``       — list instances visible to the caller.
  ``GET    /api/instances/{id}``  — fetch a single instance by id.
  ``DELETE /api/instances/{id}``  — remove a registered instance.

All endpoints are JWT-protected and use ``@translate_app_errors`` to convert
domain exceptions to ``HTTPException`` automatically.
"""

from typing import Any, List

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import CreateTritonInstanceRequest, TritonInstanceDTO, UpdateTritonInstanceRequest
from app.services.triton import instances as instance_service

router = APIRouter(prefix="/api/instances", tags=["instances"])


@router.post("", response_model=TritonInstanceDTO)
@translate_app_errors
async def create_instance(
    request: CreateTritonInstanceRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TritonInstanceDTO:
    """Create a Triton instance from UI input (url + optional name).

    The backend connects to Triton, reads the currently available models,
    stores the instance in the DB, and returns the created DTO.
    """
    return await instance_service.create_instance(request, session, claims)


@router.get("", response_model=List[TritonInstanceDTO])
@translate_app_errors
def list_instances(
    limit: int = 100,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> List[TritonInstanceDTO]:
    """List all registered Triton instances."""
    return instance_service.list_instances(session, claims, limit)


@router.get("/{instance_id}", response_model=TritonInstanceDTO)
@translate_app_errors
def get_instance(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TritonInstanceDTO:
    """Get a single Triton instance by ID."""
    return instance_service.get_instance(session, claims, instance_id)


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
@translate_app_errors
def delete_instance(
    instance_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    """Delete a registered Triton instance. Admin role required."""
    instance_service.delete_instance(session, claims, instance_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{instance_id}", response_model=TritonInstanceDTO)
@translate_app_errors
async def update_instance(
    instance_id: int,
    request: UpdateTritonInstanceRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TritonInstanceDTO:
    """Update a Triton instance endpoint and SSL verification settings."""
    return await instance_service.update_instance(request, session, claims, instance_id)


@router.get("/by-name/{name}", response_model=TritonInstanceDTO)
@translate_app_errors
def get_instance_by_name(
    name: str,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> TritonInstanceDTO:
    """Get a single Triton instance by its unique name."""
    return instance_service.get_instance_by_name(session, claims, name)
