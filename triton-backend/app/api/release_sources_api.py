"""Administrator endpoints for the release source registry."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.release_sources import ReleaseSource, ReleaseSourceUpdate
from app.schemas.serving_metadata import MetadataSource, Name
from app.services import release_sources

router = APIRouter(prefix="/api/release-sources", tags=["release-sources"])

Claims = Annotated[dict[str, Any], Depends(get_claims)]
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("")
@translate_app_errors
def list_release_sources(session: DatabaseSession, claims: Claims) -> list[ReleaseSource]:
    return release_sources.list_sources(session, claims)


@router.get("/{name}")
@translate_app_errors
def get_release_source(name: Name, session: DatabaseSession, claims: Claims) -> ReleaseSource:
    return release_sources.get_source(session, claims, name)


@router.post("", status_code=201)
@translate_app_errors
def create_release_source(source: MetadataSource, session: DatabaseSession, claims: Claims) -> ReleaseSource:
    return release_sources.save_source(session, claims, source.metadata.name, source)


@router.put("/{name}")
@translate_app_errors
def update_release_source(
    name: Name, request: ReleaseSourceUpdate, session: DatabaseSession, claims: Claims,
) -> ReleaseSource:
    return release_sources.save_source(
        session, claims, name, request.source, expected_revision=request.expected_revision,
    )


@router.delete("/{name}", status_code=204)
@translate_app_errors
def delete_release_source(
    name: Name, expected_revision: Annotated[int, Query(gt=0)], session: DatabaseSession, claims: Claims,
) -> Response:
    release_sources.delete_source(session, claims, name, expected_revision)
    return Response(status_code=204)
