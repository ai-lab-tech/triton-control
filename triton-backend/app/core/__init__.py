"""Core package — cross-cutting authentication, security, and identity utilities.

Sub-modules:
  ``access_control``  — pure role-based authorization guards.
  ``auth``            — Keycloak/OIDC JWT verification (``KeycloakAuth``).
  ``crypto``          — password hashing and secret management primitives.
  ``identity``        — JWT/session claim enrichment and user resolution.
  ``logging``         — application-wide logging configuration.
  ``security``        — FastAPI dependencies (``get_claims``, etc.).
  ``token_extractor`` — HTTP-level bearer/session token extraction.
  ``user_auth``       — local-auth JWT issue/verify utilities.
"""
