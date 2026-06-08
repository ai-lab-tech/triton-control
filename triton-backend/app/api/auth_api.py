"""Authentication, OIDC configuration, and bootstrap HTTP endpoints.

Exposes two FastAPI routers:
  ``public_router``    — unauthenticated routes (auth options, bootstrap
                         status/register, login, self-register, OIDC
                         preflight start/callback).
  ``protected_router`` — JWT-protected routes (admin user creation, OIDC
                         settings GET/PUT).

All business logic is delegated to:
  ``services/auth/bootstrap``     — initial setup flow.
  ``services/auth/local_auth``    — email/password login and registration.
  ``services/auth/oidc_preflight``— two-phase OIDC settings verification.
  ``services/auth/oidc_settings`` — admin OIDC settings read/write.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.security import get_claims
from app.db.database import get_session
from app.schemas import (
    BootstrapRegisterRequest,
    BootstrapStatusResponse,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    OidcSettingsDTO,
    SelfRegisterRequest,
    UpdateOidcSettingsRequest,
    UserDTO,
)
from app.services.auth.bootstrap import auth_options, bootstrap_register, bootstrap_status
from app.services.auth.local_auth import login, register_user, self_register
from app.services.auth.oidc_preflight import (
    oidc_preflight_callback,
    start_oidc_preflight,
)
from app.services.auth.oidc_settings import get_oidc_settings, put_oidc_settings

public_router = APIRouter(prefix="/api/auth", tags=["users"])
protected_router = APIRouter(prefix="/api/auth", tags=["users"])


@public_router.get("/options")
def auth_options_endpoint(session: Session = Depends(get_session)) -> dict[str, object]:
    return auth_options(session)


@public_router.get("/bootstrap-status", response_model=BootstrapStatusResponse)
def bootstrap_status_endpoint(session: Session = Depends(get_session)) -> BootstrapStatusResponse:
    return bootstrap_status(session)


@public_router.post("/bootstrap/register", response_model=UserDTO)
@translate_app_errors
def bootstrap_register_endpoint(
    request: BootstrapRegisterRequest,
    session: Session = Depends(get_session),
) -> UserDTO:
    return bootstrap_register(request, session)


@protected_router.get("/settings", response_model=OidcSettingsDTO)
@translate_app_errors
def get_oidc_settings_endpoint(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> OidcSettingsDTO:
    return get_oidc_settings(session, claims)


@protected_router.put("/settings")
@translate_app_errors
async def put_oidc_settings_endpoint(
    request: UpdateOidcSettingsRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> object:
    return await put_oidc_settings(request, http_request, session, claims)


@protected_router.post("/settings/preflight/start")
@translate_app_errors
async def start_oidc_preflight_endpoint(
    request: UpdateOidcSettingsRequest,
    http_request: Request,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> object:
    return await start_oidc_preflight(request, http_request, claims)


@public_router.get("/settings/preflight/callback", name="oidc_preflight_callback")
async def oidc_preflight_callback_endpoint(
    http_request: Request, session: Session = Depends(get_session)
) -> object:
    return await oidc_preflight_callback(http_request, session)


@protected_router.post("/register", response_model=UserDTO)
@translate_app_errors
def register_user_endpoint(
    request: CreateUserRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> UserDTO:
    return register_user(request, session, claims)


@public_router.post("/self-register", response_model=UserDTO)
@translate_app_errors
def self_register_endpoint(
    request: SelfRegisterRequest, session: Session = Depends(get_session)
) -> UserDTO:
    return self_register(request, session)


@public_router.post("/login", response_model=LoginResponse)
@translate_app_errors
def login_endpoint(
    request: LoginRequest, session: Session = Depends(get_session)
) -> LoginResponse:
    return login(request, session)
