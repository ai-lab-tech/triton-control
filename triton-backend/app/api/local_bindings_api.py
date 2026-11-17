"""Admin-only bindings and sanitized admission diagnostics; no credential export."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Response
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas.local_bindings import (
    AdmissionRequest,
    AdmissionResult,
    CredentialInput,
    CredentialResponse,
    PolicyResponse,
    PolicyUpdate,
)
from app.schemas.serving_metadata import Name
from app.services import local_bindings

router = APIRouter(prefix="/api/local-bindings", tags=["local-bindings"])


@router.get("/policy")
@translate_app_errors
def get_policy(session: Session = Depends(get_session), claims: dict[str, Any] = Depends(get_claims)) -> PolicyResponse:
    return local_bindings.get_policy(session, claims)


@router.put("/policy")
@translate_app_errors
def put_policy(
    request: PolicyUpdate,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> PolicyResponse:
    return local_bindings.update_policy(session, claims, request)


@router.put("/credentials/{name}")
@translate_app_errors
def put_credential(
    name: Name,
    request: Annotated[CredentialInput, Body()],
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> CredentialResponse:
    return local_bindings.put_credential(session, claims, name, request)


@router.get("/credentials/{name}")
@translate_app_errors
def get_credential(
    name: Name,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> CredentialResponse:
    return local_bindings.get_credential(session, claims, name)


@router.delete("/credentials/{name}", status_code=204)
@translate_app_errors
def delete_credential(
    name: Name,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> Response:
    local_bindings.delete_credential(session, claims, name)
    return Response(status_code=204)


@router.post("/admissions")
@translate_app_errors
def admit(
    request: AdmissionRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> AdmissionResult:
    return local_bindings.admit(session, claims, request)
