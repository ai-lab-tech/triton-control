"""Reusable S3 deployment profiles owned by users."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlmodel import Session

from app.core.access_control import require_member_or_admin
from app.core.crypto import decrypt_secret, encrypt_secret, hash_secret, is_secret_set
from app.core.identity import require_user_entity
from app.db.entities import S3ProfileEntity
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.repositories import s3_profiles
from app.schemas import CreateS3ProfileRequest, S3ProfileDTO, UpdateS3ProfileRequest


def list_profiles(session: Session, claims: dict[str, Any]) -> list[S3ProfileDTO]:
    require_member_or_admin(claims)
    owner = require_user_entity(session, claims)
    return [_to_dto(profile) for profile in s3_profiles.list_for_owner(session, owner.id or 0)]


def create_profile(
    session: Session,
    claims: dict[str, Any],
    request: CreateS3ProfileRequest,
) -> S3ProfileDTO:
    require_member_or_admin(claims)
    owner = require_user_entity(session, claims)
    owner_id = owner.id or 0
    name = _clean_required(request.name, "Profile name")
    if s3_profiles.find_by_name_for_owner(session, owner_id, name):
        raise ConflictError("S3 profile name already exists")
    secret = _clean_required(request.secret_key, "S3 secret key")
    profile = s3_profiles.create(
        session,
        owner_user_id=owner_id,
        name=name,
        endpoint=_normalize_endpoint(request.endpoint),
        bucket=_clean_required(request.bucket, "S3 bucket"),
        region=_clean_optional(request.region) or "us-east-1",
        access_key=_clean_required(request.access_key, "S3 access key"),
        secret_key_hash=hash_secret(secret),
        secret_key_enc=encrypt_secret(secret),
        prefix=_clean_prefix(request.prefix),
        force_path_style=bool(request.force_path_style),
        ca_certificate=(request.ca_certificate or "").strip(),
    )
    return _to_dto(profile)


def update_profile(
    session: Session,
    claims: dict[str, Any],
    profile_id: int,
    request: UpdateS3ProfileRequest,
) -> S3ProfileDTO:
    require_member_or_admin(claims)
    owner = require_user_entity(session, claims)
    profile = _get_profile(session, owner.id or 0, profile_id)
    if request.name is not None:
        name = _clean_required(request.name, "Profile name")
        existing = s3_profiles.find_by_name_for_owner(session, owner.id or 0, name)
        if existing and existing.id != profile.id:
            raise ConflictError("S3 profile name already exists")
        profile.name = name
    if request.endpoint is not None:
        profile.endpoint = _normalize_endpoint(request.endpoint)
    if request.bucket is not None:
        profile.bucket = _clean_required(request.bucket, "S3 bucket")
    if request.region is not None:
        profile.region = _clean_optional(request.region) or "us-east-1"
    if request.access_key is not None:
        profile.access_key = _clean_required(request.access_key, "S3 access key")
    if request.secret_key is not None and is_secret_set(request.secret_key):
        secret = _clean_required(request.secret_key, "S3 secret key")
        profile.secret_key_hash = hash_secret(secret)
        profile.secret_key_enc = encrypt_secret(secret)
    if request.prefix is not None:
        profile.prefix = _clean_prefix(request.prefix)
    if request.force_path_style is not None:
        profile.force_path_style = bool(request.force_path_style)
    if request.ca_certificate is not None:
        profile.ca_certificate = request.ca_certificate.strip()
    profile.updated_at = datetime.utcnow()
    return _to_dto(s3_profiles.save(session, profile))


def delete_profile(session: Session, claims: dict[str, Any], profile_id: int) -> dict[str, str]:
    require_member_or_admin(claims)
    owner = require_user_entity(session, claims)
    profile = _get_profile(session, owner.id or 0, profile_id)
    s3_profiles.delete(session, profile)
    return {"status": "deleted"}


def _get_profile(session: Session, owner_user_id: int, profile_id: int) -> S3ProfileEntity:
    profile = s3_profiles.find_for_owner(session, owner_user_id, profile_id)
    if not profile:
        raise NotFoundError("S3 profile not found")
    return profile


def _to_dto(profile: S3ProfileEntity) -> S3ProfileDTO:
    return S3ProfileDTO(
        id=profile.id or 0,
        name=profile.name,
        endpoint=profile.endpoint,
        bucket=profile.bucket,
        region=profile.region or "us-east-1",
        access_key=profile.access_key,
        secret_key=decrypt_secret(profile.secret_key_enc or ""),
        prefix=profile.prefix or "",
        force_path_style=bool(profile.force_path_style),
        ca_certificate=profile.ca_certificate or "",
    )


def _normalize_endpoint(endpoint: str | None) -> str:
    value = _clean_required(endpoint, "S3 endpoint").rstrip("/")
    if not (value.startswith("http://") or value.startswith("https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadRequestError("S3 endpoint must be an http(s) URL")
    return value


def _clean_required(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise BadRequestError(f"{label} is required")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def _clean_prefix(value: str | None) -> str:
    return (value or "").strip().replace("\\", "/").strip("/")
