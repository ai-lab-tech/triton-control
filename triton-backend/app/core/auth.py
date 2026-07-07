"""Keycloak/OIDC JWT verification.

Provides ``KeycloakAuth``, an async verifier that fetches OIDC discovery
documents and JWKS from a Keycloak realm, validates bearer tokens with
``PyJWT``, and caches remote documents with a configurable TTL to
avoid redundant network round-trips.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx
from cachetools import TTLCache
import jwt
from jwt.types import Options


class KeycloakAuth:
    def __init__(self, issuer: str, expected_audience: Optional[str] = None, cache_ttl_seconds: int = 600):
        self.issuer = issuer.rstrip("/")
        self.expected_audience = expected_audience
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=16, ttl=cache_ttl_seconds)

    async def _get_oidc_config(self) -> Dict[str, Any]:
        key = "oidc_config"
        if key in self._cache:
            return self._cache[key]

        url = f"{self.issuer}/.well-known/openid-configuration"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url)
                r.raise_for_status()
                data: Dict[str, Any] = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"OIDC discovery failed: {exc}") from exc

        self._cache[key] = data
        return data

    async def _get_jwks(self) -> Dict[str, Any]:
        key = "jwks"
        if key in self._cache:
            return self._cache[key]

        cfg = await self._get_oidc_config()
        jwks_uri = cfg.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.strip():
            raise ValueError("OIDC discovery document does not contain jwks_uri")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(jwks_uri)
                r.raise_for_status()
                data: Dict[str, Any] = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError(f"OIDC JWKS fetch failed: {exc}") from exc

        self._cache[key] = data
        return data

    async def verify_token(self, token: str) -> Dict[str, Any]:
        jwks = await self._get_jwks()

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise ValueError(f"Invalid token header: {e}")

        kid = header.get("kid")
        if not kid:
            raise ValueError("Missing kid in token header")

        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)

        # Key rotation fallback: cache leeren und erneut holen
        if not key:
            self._cache.pop("jwks", None)
            jwks = await self._get_jwks()
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if not key:
                raise ValueError("Signing key not found for kid")

        options: Options = {
            "verify_aud": self.expected_audience is not None,
        }

        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.expected_audience,
                options=options,
            )
            return claims
        except jwt.PyJWTError as e:
            raise ValueError(f"Token verification failed: {e}")
