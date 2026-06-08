"""Admin-only HTTP endpoints for user management.

Mounts a router at ``/api/auth`` and exposes:
  ``GET    /api/auth/users``                      — list all users.
  ``DELETE /api/auth/users/{id}``                 — delete a user by id.
  ``PUT    /api/auth/users/{id}/instances``        — update a user's assigned
                                                     Triton instances.
  ``PUT    /api/auth/users/{id}/role``             — update a user's role.

Every endpoint requires an admin JWT claim.  Business logic is delegated
entirely to ``services/users``.
"""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import UpdateUserInstancesRequest, UpdateUserRoleRequest, UserDTO
from app.services import users as user_service

router = APIRouter(prefix="/api/auth", tags=["users"])

# Legacy aliases kept so that main.py is minimally disrupted during transition
public_router = APIRouter(prefix="/api/auth", tags=["users"])
protected_router = router


@router.get("/users", response_model=List[UserDTO])
@translate_app_errors
def list_users(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> List[UserDTO]:
    return user_service.list_users(session, claims)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@translate_app_errors
def delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> None:
    user_service.delete_user(session, claims, user_id)


@router.put("/users/{user_id}/instances", response_model=UserDTO)
@translate_app_errors
def update_user_instances(
    user_id: int,
    request: UpdateUserInstancesRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> UserDTO:
    return user_service.update_user_instances(session, claims, user_id, request)


@router.put("/users/{user_id}/role", response_model=UserDTO)
@translate_app_errors
def update_user_role(
    user_id: int,
    request: UpdateUserRoleRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> UserDTO:
    return user_service.update_user_role(session, claims, user_id, request)
