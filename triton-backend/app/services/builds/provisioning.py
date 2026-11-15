"""Reuse the installation's S3 profile without storing credentials in build evidence."""

from typing import Any

from sqlmodel import Session

from app.core.crypto import decrypt_secret
from app.exceptions import NotFoundError
from app.repositories import s3_profiles


def staging_profile(session: Session, name: str) -> dict[str, Any]:
    profiles = s3_profiles.list_by_name(session, name)
    if len(profiles) != 1:
        raise NotFoundError("One unambiguous staging S3 profile is required")
    profile = profiles[0]
    return {
        "profile_name": profile.name,
        "endpoint": profile.endpoint,
        "bucket": profile.bucket,
        "access_key": profile.access_key,
        "secret_key": decrypt_secret(profile.secret_key_enc),
        "region": profile.region,
        "ca_certificate": profile.ca_certificate,
        "profile_prefix": profile.prefix,
    }
