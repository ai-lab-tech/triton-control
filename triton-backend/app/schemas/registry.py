"""Request contracts for central registry credentials."""

import json

from pydantic import BaseModel, Field, field_validator


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
