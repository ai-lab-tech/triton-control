"""Authentication layer — FastAPI dependencies for request authentication and claim enrichment.

Public surface:
  - ``get_claims``              – FastAPI dependency, raises 401/403
  - ``get_claims_allow_pending``– same but allows accounts awaiting approval

Token extraction (bearer / session → raw claims) lives in ``core/token_extractor``.
Identity resolution (raw claims → persisted user + enriched claims) lives in ``core/identity``.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.core import identity as _identity
from app.core.token_extractor import bearer, extract_claims
from app.db.database import session_factory
from app.exceptions import AppError, ForbiddenError, UnauthorizedError


async def _get_claims_with_access_policy(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    allow_pending: bool = False,
) -> Dict[str, Any]:
    source_claims = await extract_claims(request, creds)
    try:
        with session_factory() as session:
            user = _identity.resolve_user(session, source_claims, auto_create_oidc=True)
            if not user:
                raise UnauthorizedError("User mapping not found")
            claims = _identity.claims_from_user(user, source_claims)
            if not allow_pending and not claims["access_allowed"]:
                raise ForbiddenError("Account pending admin approval")
            return claims
    except AppError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


async def get_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> Dict[str, Any]:
    return await _get_claims_with_access_policy(request, creds, allow_pending=False)


async def get_claims_allow_pending(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> Dict[str, Any]:
    return await _get_claims_with_access_policy(request, creds, allow_pending=True)
