"""OIDC service sub-package.

Sub-modules:
  ``bff``      — OAuth2 code-flow backend-for-frontend use cases (login,
                  callback, logout, whoami).
  ``config``   — OIDC settings access layer (DB or env-var source).
  ``provider`` — pure provider utilities: discovery, OAuth client, redirect
                  URLs, and connection validation.
  ``tls``      — TLS/CA-bundle configuration for OIDC provider connections.
"""
