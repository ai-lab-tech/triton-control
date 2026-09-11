"""Shared validation for user-supplied public PEM certificate bundles."""

import re
import ssl


def validate_ca_certificate(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > 262144:
        raise ValueError("CA certificate bundle must be at most 256 KiB")
    remainder = re.sub(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "", cleaned, flags=re.DOTALL,
    )
    if remainder.strip():
        raise ValueError("Invalid PEM CA certificate. Use -----BEGIN CERTIFICATE----- and -----END CERTIFICATE----- "
            "with five dashes, and include only public certificates (no private keys or other text).")
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=cleaned)
    except (ssl.SSLError, ValueError) as exc:
        raise ValueError("Invalid PEM CA certificate bundle") from exc
    return cleaned
