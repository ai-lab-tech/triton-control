"""Construct S3 providers from existing authorized profile references."""

from typing import Any
from urllib.parse import urlparse

from botocore.config import Config
from sqlmodel import Session

from app.core.access_control import require_admin
from app.core.identity import require_user_entity
from app.exceptions import ForbiddenError, NotFoundError
from app.repositories import preparation, s3_profiles
from app.services.candidates.artifacts import CaptureLimits, S3ArtifactStore, S3Source, safe_path
from app.services.storage.s3_client import S3Config, build_s3_client
from app.services.storage.s3_profiles import resolve_profile_reference


def _profile_client(session: Session, claims: dict[str, Any], profile_id: int) -> tuple[Any, str, str]:
    mapped = require_user_entity(session, claims)
    user = preparation.current_user(session, mapped.id) if mapped.id is not None else None
    if user is None or not user.is_active or user.role.strip().lower() not in {"admin", "member"}:
        raise ForbiddenError("Active member or admin required")
    profile = s3_profiles.get(session, profile_id)
    if profile is None:
        raise NotFoundError("S3 profile not found")
    # Global profile visibility and restricted-token checks remain authoritative.
    resolve_profile_reference(session, claims, profile.name)
    config = S3Config(
        s3_endpoint=profile.endpoint,
        s3_bucket=profile.bucket,
        s3_access_key=profile.access_key,
        s3_secret_key_enc=profile.secret_key_enc,
        s3_use_https=urlparse(profile.endpoint).scheme == "https",
        s3_verify_ssl=True,
        s3_ca_certificate=profile.ca_certificate,
        s3_region=profile.region,
        s3_address_style="path" if profile.force_path_style else "virtual",
    )
    client = build_s3_client(
        config,
        client_config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2, "mode": "standard"}),
    )
    prefix = profile.prefix.removesuffix("/")
    if prefix:
        safe_path(prefix, CaptureLimits())
    return client, profile.bucket, prefix


def source_from_profile(session: Session, claims: dict[str, Any], profile_id: int, relative_prefix: str) -> S3Source:
    """Single prefix beneath the selected profile; never a caller-supplied endpoint."""
    relative_prefix = safe_path(relative_prefix.removesuffix("/"), CaptureLimits())
    client, bucket, prefix = _profile_client(session, claims, profile_id)
    return S3Source(client, bucket, "/".join(part for part in (prefix, relative_prefix) if part))


def store_from_profile(
    session: Session, claims: dict[str, Any], profile_id: int, relative_prefix: str = "candidate-imports"
) -> S3ArtifactStore:
    """Admin/application setup chooses the destination, not an import payload."""
    require_admin(claims)
    mapped = require_user_entity(session, claims)
    user = preparation.current_user(session, mapped.id) if mapped.id is not None else None
    if user is None or not user.is_active or user.role.strip().lower() != "admin":
        raise ForbiddenError("Active admin required to select an artifact store")
    relative_prefix = safe_path(relative_prefix, CaptureLimits())
    client, bucket, prefix = _profile_client(session, claims, profile_id)
    return S3ArtifactStore(client, bucket, "/".join(part for part in (prefix, relative_prefix) if part))
