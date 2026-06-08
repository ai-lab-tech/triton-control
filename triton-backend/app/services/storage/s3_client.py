"""Low-level S3 client factory and related utilities.

Decouples ``boto3`` client construction from the rest of the codebase so
service modules do not need to import ORM entities or repeat credential
handling logic.

Provides:
  ``S3Config``          — ``TypedDict`` with the minimal fields needed to
                           build a ``boto3`` client (endpoint, bucket, region,
                           credentials, address style, TLS flags).
  ``build_s3_client(entity)``  — construct a ``boto3`` S3 client from a
                                  ``TritonInstanceEntity``; decrypts the
                                  secret key and selects path/virtual
                                  addressing mode.
  ``is_s3_configured(entity)`` — quick guard that returns ``True`` only when
                                  all required S3 fields are populated.
  ``format_s3_error(error)``   — convert a ``botocore.ClientError`` into a
                                  human-readable string for API responses.
"""

from __future__ import annotations

import tempfile
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.httpsession import DEFAULT_CA_BUNDLE

from app.core.crypto import decrypt_secret
from app.core.tls import create_default_context_with_extra_ca

if TYPE_CHECKING:
    from app.db.entities import TritonInstanceEntity


class S3Config(TypedDict, total=False):
    """Minimal S3 configuration needed to build a boto3 client.

    Using a TypedDict decouples ``build_s3_client`` from the ORM entity so
    the function can be called from any context (tests, background tasks, etc.)
    without importing database entities.
    """

    s3_endpoint: str | None
    s3_bucket: str | None
    s3_access_key: str | None
    s3_secret_key_enc: str | None
    s3_use_https: bool
    s3_verify_ssl: bool | None
    s3_ca_certificate: str
    s3_region: str | None
    s3_address_style: str


def _entity_to_s3_config(entity: TritonInstanceEntity) -> S3Config:
    """Convert a ``TritonInstanceEntity`` to the ``S3Config`` TypedDict."""
    return S3Config(
        s3_endpoint=entity.s3_endpoint,
        s3_bucket=entity.s3_bucket,
        s3_access_key=entity.s3_access_key,
        s3_secret_key_enc=entity.s3_secret_key_enc,
        s3_use_https=bool(entity.s3_use_https),
        s3_verify_ssl=entity.s3_verify_ssl,
        s3_ca_certificate=entity.s3_ca_certificate or "",
        s3_region=entity.s3_region,
        s3_address_style=entity.s3_address_style or "path",
    )


def _write_ca_certificate_file(certificate: str) -> str:
    certificate = certificate.strip()
    create_default_context_with_extra_ca(certificate, "S3")

    digest = sha256(certificate.encode("utf-8")).hexdigest()
    ca_dir = Path(tempfile.gettempdir()) / "triton_backend_s3_ca"
    ca_dir.mkdir(parents=True, exist_ok=True)
    ca_path = ca_dir / f"{digest}.pem"
    if not ca_path.exists():
        ca_path.write_text(_build_ca_bundle(certificate), encoding="utf-8")
    return str(ca_path)


def _build_ca_bundle(certificate: str) -> str:
    default_ca_path = Path(DEFAULT_CA_BUNDLE)
    bundle_parts: list[str] = []
    if default_ca_path.is_file():
        bundle_parts.append(default_ca_path.read_text(encoding="utf-8").strip())
    bundle_parts.append(certificate.strip())
    return "\n\n".join(part for part in bundle_parts if part) + "\n"


def is_s3_configured(entity: TritonInstanceEntity) -> bool:
    """Return True when the entity has all required S3 fields set."""
    return bool(
        entity.s3_endpoint
        and entity.s3_bucket
        and entity.s3_access_key
        and entity.s3_secret_key_enc
    )


def build_s3_client(entity_or_config: TritonInstanceEntity | S3Config) -> Any:
    """Build a boto3 S3 client from a ``TritonInstanceEntity`` or ``S3Config``.

    The caller is responsible for ensuring that ``s3_endpoint``,
    ``s3_bucket``, ``s3_access_key``, and ``s3_secret_key_enc`` are set
    before calling this function (e.g. via ``is_s3_configured``).
    """
    cfg: S3Config
    if isinstance(entity_or_config, dict):
        cfg = entity_or_config
    else:
        cfg = _entity_to_s3_config(entity_or_config)

    secret = decrypt_secret(cfg.get("s3_secret_key_enc") or "")
    endpoint = _normalize_endpoint_for_client(cfg.get("s3_endpoint"))
    use_https = bool(cfg.get("s3_use_https", False)) or urlparse((endpoint or "").strip()).scheme == "https"
    configured_verify_ssl = cfg.get("s3_verify_ssl")
    verify_ssl = use_https if configured_verify_ssl is None else bool(configured_verify_ssl)
    ca_certificate = (cfg.get("s3_ca_certificate") or "").strip()
    ca_bundle = (
        _write_ca_certificate_file(ca_certificate)
        if ca_certificate and verify_ssl
        else ""
    )
    verify: bool | str = ca_bundle if (verify_ssl and ca_bundle) else verify_ssl
    address_style = cfg.get("s3_address_style") or "path"

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=cfg.get("s3_access_key"),
        aws_secret_access_key=secret,
        region_name=cfg.get("s3_region"),
        use_ssl=use_https,
        verify=verify,
        config=Config(s3={"addressing_style": cast(Literal["auto", "virtual", "path"], address_style)}),
    )


def _normalize_endpoint_for_client(endpoint: str | None) -> str | None:
    value = (endpoint or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme == "http" and parsed.port == 443:
        return f"https://{parsed.netloc}{parsed.path}".rstrip("/")
    return value


def format_s3_error(exc: BotoCoreError | ClientError) -> str:
    """Extract a human-readable error string from a botocore exception."""
    if not isinstance(exc, ClientError):
        return str(exc) or exc.__class__.__name__

    error = exc.response.get("Error") or {}
    code = error.get("Code")
    message = error.get("Message")
    parts = [part for part in [code, message] if part]
    if parts:
        return ": ".join(parts)
    return "Unknown S3 error"
