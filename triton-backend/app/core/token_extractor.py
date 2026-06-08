"""HTTP-level token extraction — the only module allowed to raise ``HTTPException`` for auth failures.

Separates the concern of *extracting raw claims from an HTTP request* (bearer token
or session cookie) from the higher-level access-policy check in ``core/security.py``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, cast

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import KeycloakAuth
from app.core.user_auth import verify_access_token

bearer = HTTPBearer(auto_error=False)

ISSUER = (
    os.getenv("OIDC_ISSUER", "").strip()
    or "http://localhost:8080/realms/master"
)

auth = KeycloakAuth(issuer=ISSUER, expected_audience=None)


async def extract_claims(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer),
) -> Dict[str, Any]:
    """Extract raw claims from a bearer token (local JWT or Keycloak) or a session cookie.

    Raises ``HTTPException(401)`` when no valid credential is present — this is the
    intentional HTTP boundary; all callers are FastAPI dependencies.
    """
    if creds and creds.scheme.lower() == "bearer":
        try:
            return verify_access_token(creds.credentials)
        except ValueError as local_exc:
            try:
                claims = await auth.verify_token(creds.credentials)
                claims["auth_provider"] = "oidc"
                return claims
            except ValueError as oidc_exc:
                user = request.session.get("user")
                if user:
                    return cast(Dict[str, Any], user)
                detail = str(oidc_exc) or str(local_exc) or "Invalid bearer token"
                raise HTTPException(status_code=401, detail=detail) from oidc_exc

    user = request.session.get("user")
    if user:
        return cast(Dict[str, Any], user)

    raise HTTPException(status_code=401, detail="Missing bearer token or session")
