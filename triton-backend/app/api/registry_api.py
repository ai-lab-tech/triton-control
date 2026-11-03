"""Admin-only central image pull credentials; never return secret contents."""

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session

from app.api.errors import translate_app_errors
from app.core.access_control import require_admin
from app.core.security import get_claims
from app.db.database import get_session
from app.db.entities import RegistryConfigEntity
from app.services.registry_settings import encrypt_registry_secret

router = APIRouter(prefix="/api/settings/registry", tags=["settings"])


class RegistryUpdate(BaseModel):
    dockerconfigjson: str = Field(max_length=500_000, repr=False)

    @field_validator("dockerconfigjson")
    @classmethod
    def validate_config(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        try:
            config = json.loads(value)
            auths = config.get("auths") if isinstance(config, dict) else None
            if not isinstance(auths, dict) or not auths:
                raise ValueError
            if not all(key and isinstance(auth, dict) for key, auth in auths.items()):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Enter Docker config JSON containing a non-empty auths object") from None
        return value


@router.get("")
@translate_app_errors
def get_registry_settings(
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, bool]:
    require_admin(claims)
    row = session.get(RegistryConfigEntity, 1)
    return {"configured": bool(row and row.dockerconfigjson_enc)}


@router.put("")
@translate_app_errors
def put_registry_settings(
    request: RegistryUpdate,
    session: Session = Depends(get_session),
    claims: dict[str, Any] = Depends(get_claims),
) -> dict[str, bool]:
    require_admin(claims)
    row = session.get(RegistryConfigEntity, 1) or RegistryConfigEntity()
    row.dockerconfigjson_enc = encrypt_registry_secret(request.dockerconfigjson)
    session.add(row)
    session.commit()
    return {"configured": bool(request.dockerconfigjson)}
