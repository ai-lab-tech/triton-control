"""API router package — groups all FastAPI routers for the triton-backend.

Each sub-module registers one or more ``APIRouter`` instances that are
imported and mounted by ``app/main.py``:
  ``auth_api``      — authentication, OIDC config, and bootstrap endpoints.
  ``dashboard_api`` — dashboard alert endpoints.
  ``errors``        — ``translate_app_errors`` decorator shared by all routers.
  ``instance_api``  — Triton instance CRUD endpoints.
  ``model_api``     — Triton model repository and inference endpoints.
  ``oidc_api``      — OIDC BFF (login / callback / logout / whoami) endpoints.
  ``s3_api``        — S3 model-repository browsing and editing endpoints.
  ``user_api``      — admin user management endpoints.
"""
