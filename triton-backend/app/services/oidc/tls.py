"""TLS/CA-bundle configuration for OIDC provider connections.

Exposes ``get_oidc_tls_verify()`` which returns the value to pass as the
``verify`` argument of an ``httpx`` or ``requests`` client:
  ``False``        — disable TLS verification (``OIDC_TLS_VERIFY=false``).
  ``True``         — use the system CA store (default).
  ``str``          — path to a custom CA bundle (``OIDC_CA_BUNDLE`` env var).

The ``strict_discovery_document_validation`` flag from the stored OIDC
settings can further influence the effective TLS policy.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

from app.core.tls import create_default_context_with_extra_ca

BASE_DIR = Path(__file__).resolve().parent.parent


def resolve_app_path(value: str) -> str:
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return str(path)


def get_oidc_tls_verify(
    strict_validation: bool,
    ca_certificate: str = "",
    use_env_ca_bundle: bool = True,
) -> bool | str | ssl.SSLContext:
    if not strict_validation:
        return False

    certificate = (ca_certificate or "").strip()
    if certificate:
        return create_default_context_with_extra_ca(certificate, "OIDC")

    ca_bundle = (os.getenv("OIDC_CA_BUNDLE") or "").strip()
    if not use_env_ca_bundle:
        ca_bundle = ""
    if not ca_bundle:
        return True

    ca_bundle_path = resolve_app_path(ca_bundle)
    if not os.path.isfile(ca_bundle_path):
        raise RuntimeError(f"OIDC CA bundle file does not exist: {ca_bundle_path}")

    return ca_bundle_path
