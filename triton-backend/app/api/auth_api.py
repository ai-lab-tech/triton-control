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
    AdminResetRequest,
    BootstrapRegisterRequest,
    BootstrapStatusResponse,
    CompleteLifecycleRequest,
    CreateUserRequest,
    EmailOperationResponse,
    EmailSettingsDTO,
    ForgotPasswordRequest,
    InviteUserRequest,
    LifecycleIssueResponse,
    LoginRequest,
    LoginResponse,
    NeutralRecoveryResponse,
    OidcSettingsDTO,
    SelfRegisterRequest,
    TestEmailRequest,
    TokenInspectionRequest,
    TokenInspectionResponse,
    UpdateEmailSettingsRequest,
    UpdateOidcSettingsRequest,
    UserDTO,
)
from app.services.auth.account_lifecycle import (
    activate_invitation,
    complete_password_reset,
    forgot_password,
    inspect_token,
    invite_user,
    issue_admin_reset,
    reissue_invitation,
    revoke_invitation,
)
from app.services.auth.bootstrap import auth_options, bootstrap_register, bootstrap_status
from app.services.auth.email_settings import (
    get_email_settings,
    test_email,
    update_email_settings,
)
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
    request: LoginRequest,
    http_request: Request,
    session: Session = Depends(get_session),
) -> LoginResponse:
    response = login(request, session)
    user = response.user
    http_request.session["user"] = {
        "sub": user.email,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "auth_provider": user.auth_provider,
        "access_allowed": user.is_active,
        "credential_version": user.credential_version,
    }
    return response


@protected_router.post("/invitations", response_model=LifecycleIssueResponse)
@translate_app_errors
def invite_user_endpoint(
    request: InviteUserRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> LifecycleIssueResponse:
    return invite_user(request, session, claims)


@protected_router.post("/invitations/{user_id}/reissue", response_model=LifecycleIssueResponse)
@translate_app_errors
def reissue_invitation_endpoint(
    user_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> LifecycleIssueResponse:
    return reissue_invitation(user_id, session, claims)


@protected_router.delete("/invitations/{user_id}")
@translate_app_errors
def revoke_invitation_endpoint(
    user_id: int,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, bool]:
    return revoke_invitation(user_id, session, claims)


@public_router.post("/invitations/inspect", response_model=TokenInspectionResponse)
def inspect_invitation_endpoint(
    request: TokenInspectionRequest, session: Session = Depends(get_session)
) -> TokenInspectionResponse:
    return inspect_token(session, request.token, "invite")


@public_router.post("/invitations/activate")
@translate_app_errors
def activate_invitation_endpoint(
    request: CompleteLifecycleRequest, session: Session = Depends(get_session)
) -> dict[str, bool]:
    return activate_invitation(request, session)


@public_router.post("/forgot-password", response_model=NeutralRecoveryResponse)
@translate_app_errors
def forgot_password_endpoint(
    request: ForgotPasswordRequest,
    http_request: Request,
    session: Session = Depends(get_session),
) -> NeutralRecoveryResponse:
    client_key = http_request.client.host if http_request.client else "unknown"
    return forgot_password(request, session, client_key)


@protected_router.post("/password-resets", response_model=LifecycleIssueResponse)
@translate_app_errors
def admin_reset_endpoint(
    request: AdminResetRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> LifecycleIssueResponse:
    return issue_admin_reset(request.user_id, session, claims)


@public_router.post("/password-resets/inspect", response_model=TokenInspectionResponse)
def inspect_reset_endpoint(
    request: TokenInspectionRequest, session: Session = Depends(get_session)
) -> TokenInspectionResponse:
    return inspect_token(session, request.token, "password_reset")


@public_router.post("/password-resets/complete")
@translate_app_errors
def complete_reset_endpoint(
    request: CompleteLifecycleRequest, session: Session = Depends(get_session)
) -> dict[str, bool]:
    return complete_password_reset(request, session)


@protected_router.get("/email-settings", response_model=EmailSettingsDTO)
@translate_app_errors
def get_email_settings_endpoint(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> EmailSettingsDTO:
    return get_email_settings(session, claims)


@protected_router.put("/email-settings", response_model=EmailSettingsDTO)
@translate_app_errors
def update_email_settings_endpoint(
    request: UpdateEmailSettingsRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> EmailSettingsDTO:
    return update_email_settings(request, session, claims)


@protected_router.post("/email-settings/test", response_model=EmailOperationResponse)
@translate_app_errors
def test_email_endpoint(
    request: TestEmailRequest,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> EmailOperationResponse:
    return test_email(request, session, claims)
