"""OIDC Backend-for-Frontend (BFF) HTTP endpoints.

Handles the browser-facing OAuth2 authorisation code flow:
  ``GET  /login``          — initiate the OIDC redirect to the provider.
  ``GET  /auth/callback``  — receive the authorisation code, exchange it for
                              tokens, and write the user into the session.
  ``POST /logout``         — clear the session and return a redirect.
  ``GET  /api/whoami``     — return session user info or 401.

Business logic (token exchange, session management, user auto-creation)
is delegated to ``services/oidc/bff``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from app.db.database import get_session
from app.services.oidc import bff as oidc_bff_service

router = APIRouter(tags=["auth"])


@router.get("/login")
async def login(request: Request, session: Session = Depends(get_session)) -> object:
    return await oidc_bff_service.login(request, session)


@router.get("/auth/callback")
async def auth_callback(request: Request, session: Session = Depends(get_session)) -> object:
    return await oidc_bff_service.auth_callback(request, session)


@router.post("/logout")
async def logout(request: Request) -> dict[str, bool]:
    return oidc_bff_service.logout(request)


@router.get("/api/whoami")
async def whoami(request: Request, session: Session = Depends(get_session)) -> object:
    return oidc_bff_service.whoami(request, session)
