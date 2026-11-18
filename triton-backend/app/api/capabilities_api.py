"""Authenticated installation capabilities are ceilings, never resource grants."""

from typing import Any

from fastapi import APIRouter, Depends

from app.core.installation_policy import capabilities
from app.core.security import get_claims
from app.schemas.capabilities import Capabilities

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("", response_model=Capabilities)
def read_capabilities(claims: dict[str, Any] = Depends(get_claims)) -> Capabilities:
    return capabilities(str(claims.get("role", "")))
