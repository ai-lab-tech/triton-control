from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.access_groups import AccessGroupDTO, AccessGroupInstanceOption, AccessGroupRequest, AccessGroupUpdate
from app.services import access_groups

router = APIRouter(prefix="/api/access-groups", tags=["access-groups"])
DatabaseSession = Annotated[Session, Depends(get_session)]
Claims = Annotated[dict[str, Any], Depends(get_claims)]


@router.get("")
@translate_app_errors
def list_groups(session: DatabaseSession, claims: Claims) -> list[AccessGroupDTO]:
    return access_groups.list_groups(session, claims)


@router.get("/instance-options")
@translate_app_errors
def list_instance_options(session: DatabaseSession, claims: Claims) -> list[AccessGroupInstanceOption]:
    return access_groups.list_instance_options(session, claims)


@router.post("", status_code=201)
@translate_app_errors
def create_group(request: AccessGroupRequest, session: DatabaseSession, claims: Claims) -> AccessGroupDTO:
    return access_groups.create_group(session, claims, request)


@router.put("/{group_id}")
@translate_app_errors
def update_group(
    group_id: UUID, request: AccessGroupUpdate, session: DatabaseSession, claims: Claims,
) -> AccessGroupDTO:
    return access_groups.update_group(session, claims, group_id, request)


@router.delete("/{group_id}", status_code=204)
@translate_app_errors
def delete_group(
    group_id: UUID, revision: Annotated[int, Query(ge=1)], session: DatabaseSession, claims: Claims,
) -> None:
    access_groups.delete_group(session, claims, group_id, revision)
