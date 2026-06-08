"""Shared TLS helpers for combining default trust with uploaded CA certificates."""

from __future__ import annotations

import ssl


def create_default_context_with_extra_ca(ca_certificate: str, label: str) -> ssl.SSLContext:
    """Create a default SSL context and append a PEM CA certificate or chain."""
    certificate = (ca_certificate or "").strip()
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cadata=certificate)
    except ssl.SSLError as exc:
        raise RuntimeError(f"{label} CA certificate is not a valid PEM certificate") from exc
    return context
