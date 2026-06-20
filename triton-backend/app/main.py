"""FastAPI application factory and startup wiring.

This module is the real entry-point for the backend.  It:
  - Creates the ``FastAPI`` instance with title, description, and version.
  - Registers all API routers (auth, OIDC, instances, models, S3, users,
    dashboard) under their respective path prefixes.
  - Adds ``SessionMiddleware`` (Starlette cookie-based sessions) and
    ``CORSMiddleware`` (configurable via environment variables).
  - Calls ``init_db()`` at startup to create missing tables.
  - Starts the ``InstanceHealthRefresher`` background task on startup and
    stops it cleanly on shutdown.

Environment variables consumed here:
  ``SESSION_SECRET``       — signing key for session cookies.
  ``SESSION_HTTPS_ONLY``   — restrict session cookies to HTTPS.
  ``SESSION_MAX_AGE_SECONDS`` — optional cookie/session lifetime in seconds.
  ``SERVER_HTTPS_ENABLED`` — whether the server itself runs on HTTPS.
  ``CORS_ORIGINS``         — comma-separated list of allowed origins.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.api.auth_api import protected_router as protected_auth_router
from app.api.auth_api import public_router as public_auth_router
from app.api.dashboard_api import router as dashboard_router
from app.api.deployment_api import router as deployment_router
from app.api.development_api import router as code_server_router
from app.api.instance_api import router as instance_router
from app.api.model_api import router as model_router
from app.api.mlflow_api import router as mlflow_router
from app.api.oidc_api import router as oidc_router
from app.api.perf_analyzer_api import router as perf_analyzer_router
from app.api.s3_api import router as s3_router
from app.api.user_api import router as user_router
from app.api.workflows_api import router as workflows_router
from app.core.logging import configure_logging, get_log_level_name, is_verbose_logging
from app.core.security import get_claims, get_claims_allow_pending
from app.db.database import init_db
from app.services.triton.client import TritonService
from app.services.triton.health import instance_health_refresher

configure_logging()

app = FastAPI(
    title="Triton Backend",
    description="Backend for Triton",
    version="1.0.0",
)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed > 0 else None


session_secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
session_https_only = _parse_bool(os.getenv("SESSION_HTTPS_ONLY"), default=False)
server_https_enabled = _parse_bool(os.getenv("SERVER_HTTPS_ENABLED"), default=False)
session_max_age = _parse_positive_int(os.getenv("SESSION_MAX_AGE_SECONDS"))

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    https_only=session_https_only,
    same_site=os.getenv("SESSION_SAMESITE", "lax"),
    session_cookie=os.getenv("SESSION_COOKIE", "session"),
    max_age=session_max_age or 14 * 24 * 60 * 60,
)

cors_origins = _parse_csv(
    os.getenv(
        "CORS_ORIGINS",
        "http://localhost:4200,http://127.0.0.1:4200,https://localhost:4200,https://127.0.0.1:4200",
    )
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    """Initialize database tables on startup."""
    init_db()
    instance_health_refresher.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Stop background workers."""
    await instance_health_refresher.stop()
    await TritonService.close_all_clients()


app.include_router(oidc_router)
app.include_router(public_auth_router)
app.include_router(protected_auth_router, dependencies=[Depends(get_claims)])
app.include_router(user_router, dependencies=[Depends(get_claims)])
app.include_router(dashboard_router, dependencies=[Depends(get_claims)])
app.include_router(deployment_router, dependencies=[Depends(get_claims)])
app.include_router(code_server_router)
app.include_router(perf_analyzer_router, dependencies=[Depends(get_claims)])
app.include_router(mlflow_router)
app.include_router(instance_router, dependencies=[Depends(get_claims)])
app.include_router(model_router, dependencies=[Depends(get_claims)])
app.include_router(s3_router, dependencies=[Depends(get_claims)])
app.include_router(workflows_router)


@app.get("/api/auth/me")
async def auth_me(
    request: Request,
    claims: dict[str, Any] = Depends(get_claims_allow_pending),
) -> dict[str, Any]:
    request.session["user"] = {
        "sub": claims.get("sub") or claims.get("email") or claims.get("user_id"),
        "email": claims.get("email"),
        "name": claims.get("name"),
        "role": claims.get("role"),
        "auth_provider": claims.get("auth_provider"),
        "access_allowed": bool(claims.get("access_allowed", True)),
        "user_id": claims.get("user_id"),
    }
    return {
        "authenticated": True,
        "access_allowed": bool(claims.get("access_allowed", True)),
        "user": claims,
    }


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"message": "Hello from Triton Backend!"}


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}


def run() -> None:
    import uvicorn

    uvicorn_config: dict[str, Any] = {
        "app": "app.main:app",
        "port": 8000,
        "reload": True,
        "log_level": get_log_level_name().lower(),
        "access_log": is_verbose_logging(),
    }

    if server_https_enabled:
        uvicorn_config.update(
            ssl_keyfile=os.getenv("TLS_KEY_FILE", "./tls/key.pem"),
            ssl_certfile=os.getenv("TLS_CERT_FILE", "./tls/cert.pem"),
        )

    uvicorn.run(**uvicorn_config)


if __name__ == "__main__":
    run()
