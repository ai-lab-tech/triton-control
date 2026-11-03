"""Central registry credential configuration for managed workloads."""

import base64
import hashlib
import os
from typing import TypeVar

from cryptography.fernet import Fernet, InvalidToken
from sqlmodel import Session, SQLModel

from app.db.entities import RegistryConfigEntity
from app.exceptions import BadRequestError

RequestT = TypeVar("RequestT", bound=SQLModel)


def apply_registry_settings(request: RequestT, session: Session) -> RequestT:
    if getattr(request, "dockerconfigjson", None):
        raise BadRequestError("Registry credentials must be configured in admin Settings")
    row = session.get(RegistryConfigEntity, 1)
    secret = decrypt_registry_secret(row.dockerconfigjson_enc) if row and row.dockerconfigjson_enc else None
    return request.model_copy(update={"dockerconfigjson": secret or None})


def _cipher() -> Fernet:
    key = os.getenv("REGISTRY_SECRET_ENCRYPTION_KEY") or os.getenv("SESSION_SECRET")
    if not key:
        raise BadRequestError(
            "Configure REGISTRY_SECRET_ENCRYPTION_KEY or a stable SESSION_SECRET before saving registry credentials"
        )
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt_registry_secret(value: str) -> str:
    return _cipher().encrypt(value.encode()).decode() if value else ""


def decrypt_registry_secret(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise BadRequestError("Registry credentials cannot be decrypted; save them again in admin Settings") from None
